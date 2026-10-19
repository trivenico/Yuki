"""Persistent SQLite queue for bounded Yuki worker processes.

Stores task metadata only: it does not execute tools, start workers, or bypass
human approvals. Callers must authorize jobs before enqueue and before effects.
"""
from __future__ import annotations
import json
import sqlite3
from contextlib import closing
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STATUSES = {"queued", "running", "completed", "failed", "cancelled"}

@dataclass(frozen=True)
class Job:
    id: str
    task_type: str
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    created_at: float
    updated_at: float
    worker_id: str | None = None
    lease_until: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

class SQLiteJobQueue:
    """Durable queue with atomic claims, leases, retries and cancellation."""
    def __init__(self, database: str | Path, *, busy_timeout_ms: int = 5000):
        self.database = str(database)
        self.busy_timeout_ms = int(busy_timeout_ms)
        if not 1 <= self.busy_timeout_ms <= 60000:
            raise ValueError("busy_timeout_ms must be between 1 and 60000.")
        if self.database != ":memory:":
            Path(self.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.database, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize(self):
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, task_type TEXT NOT NULL, payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled')),
                attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                worker_id TEXT, lease_until REAL, result_json TEXT, error TEXT
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, created_at)")

    @staticmethod
    def _validate_task_type(task_type):
        if not isinstance(task_type, str) or not task_type.strip() or len(task_type) > 120:
            raise ValueError("task_type must be non-empty text of at most 120 characters.")
        if any(ord(ch) < 32 for ch in task_type):
            raise ValueError("task_type contains control characters.")
        return task_type.strip()

    @staticmethod
    def _validate_payload(payload):
        if not isinstance(payload, dict):
            raise TypeError("payload must be a JSON object.")
        try:
            encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must contain finite, JSON-serializable values.") from exc
        if len(encoded.encode("utf-8")) > 256_000:
            raise ValueError("payload exceeds 256 KB.")
        return encoded

    @staticmethod
    def _row(row):
        if row is None:
            return None
        return Job(
            id=row["id"], task_type=row["task_type"], payload=json.loads(row["payload_json"]),
            status=row["status"], attempts=row["attempts"], max_attempts=row["max_attempts"],
            created_at=row["created_at"], updated_at=row["updated_at"], worker_id=row["worker_id"],
            lease_until=row["lease_until"], result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
        )

    def enqueue(self, task_type: str, payload: dict[str, Any], *, max_attempts: int = 3, job_id: str | None = None) -> Job:
        task_type = self._validate_task_type(task_type)
        payload_json = self._validate_payload(payload)
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be an integer from 1 to 10.")
        job_id = job_id or str(uuid.uuid4())
        if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 120:
            raise ValueError("job_id must be non-empty text of at most 120 characters.")
        now = time.time()
        with closing(self._connect()) as conn:
            conn.execute("INSERT INTO jobs(id,task_type,payload_json,status,attempts,max_attempts,created_at,updated_at) VALUES(?,?,?,'queued',0,?,?,?)",
                         (job_id, task_type, payload_json, max_attempts, now, now))
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(row)

    def claim_next(self, worker_id: str, *, lease_seconds: int = 60, now: float | None = None) -> Job | None:
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 120:
            raise ValueError("worker_id must be non-empty text of at most 120 characters.")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be an integer from 1 to 3600.")
        now = time.time() if now is None else float(now)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE jobs SET status=CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END, "
                "worker_id=NULL, lease_until=NULL, updated_at=?, "
                "error=CASE WHEN attempts >= max_attempts THEN 'Worker lease expired; retry budget exhausted.' ELSE error END "
                "WHERE status='running' AND lease_until <= ?", (now, now))
            row = conn.execute("SELECT id FROM jobs WHERE status='queued' AND attempts < max_attempts ORDER BY created_at, id LIMIT 1").fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute("UPDATE jobs SET status='running',worker_id=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=? AND status='queued'",
                         (worker_id.strip(), now + lease_seconds, now, row["id"]))
            claimed = conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
            conn.commit()
        return self._row(claimed)

    def complete(self, job_id: str, worker_id: str, result: dict[str, Any] | None = None) -> Job:
        result_json = self._validate_payload({} if result is None else result)
        return self._finish(job_id, worker_id, "completed", result_json=result_json)

    def fail(self, job_id: str, worker_id: str, error: str, *, retry: bool = True) -> Job:
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must be non-empty text.")
        error = error[:2000]
        now = time.time()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            self._require_owner(row, worker_id)
            status = "queued" if retry and row["attempts"] < row["max_attempts"] else "failed"
            conn.execute("UPDATE jobs SET status=?,worker_id=NULL,lease_until=NULL,updated_at=?,error=? WHERE id=?",
                         (status, now, error, job_id))
            updated = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            conn.commit()
        return self._row(updated)

    def _finish(self, job_id, worker_id, status, *, result_json=None):
        now = time.time()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            self._require_owner(row, worker_id)
            conn.execute("UPDATE jobs SET status=?,result_json=?,worker_id=NULL,lease_until=NULL,updated_at=?,error=NULL WHERE id=?",
                         (status, result_json, now, job_id))
            updated = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            conn.commit()
        return self._row(updated)

    @staticmethod
    def _require_owner(row, worker_id):
        if row is None:
            raise KeyError("Job not found.")
        if row["status"] != "running" or row["worker_id"] != worker_id:
            raise ValueError("Only the current worker may finish a running job.")

    def cancel(self, job_id: str) -> Job:
        now = time.time()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Job not found.")
            if row["status"] not in {"queued", "running"}:
                raise ValueError("Only queued or running jobs can be cancelled.")
            conn.execute("UPDATE jobs SET status='cancelled',worker_id=NULL,lease_until=NULL,updated_at=? WHERE id=?", (now, job_id))
            updated = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            conn.commit()
        return self._row(updated)

    def get(self, job_id: str) -> Job | None:
        with closing(self._connect()) as conn:
            return self._row(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def list_jobs(self, *, status: str | None = None, limit: int = 100) -> list[Job]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000.")
        if status is not None and status not in STATUSES:
            raise ValueError("Unknown job status.")
        with closing(self._connect()) as conn:
            if status:
                rows = conn.execute("SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(row) for row in rows]
