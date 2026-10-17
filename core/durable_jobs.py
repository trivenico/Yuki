"""SQLite-backed durable job lifecycle for local Yuki automation.

This store persists orchestration state, not executable code. Callers must validate
job payloads and execute handlers through the existing permission/sandbox boundary.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid
from typing import Any


TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
ACTIVE_STATES = frozenset({"queued", "running", "retry_wait"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be non-empty text no longer than {limit} characters.")
    return value.strip()


@dataclass(frozen=True)
class DurableJob:
    job_id: str
    task_type: str
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    idempotency_key: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    result: Any = None
    error_code: str | None = None
    cancel_requested: bool = False


class IdempotencyConflict(ValueError):
    """An idempotency key was reused for a different operation."""


class JobStore:
    """Transactional SQLite job store suitable for one local Yuki installation.

    SQLite serializes claims with BEGIN IMMEDIATE. It is not a distributed queue and
    should not be shared across hosts or network filesystems.
    """

    def __init__(self, database: str | Path, *, busy_timeout_ms: int = 5000):
        self.database = Path(database).expanduser()
        if isinstance(busy_timeout_ms, bool) or not isinstance(busy_timeout_ms, int) or not 1 <= busy_timeout_ms <= 60000:
            raise ValueError("busy_timeout_ms must be between 1 and 60000.")
        self.busy_timeout_ms = busy_timeout_ms
        self._init_lock = threading.Lock()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.database), timeout=self.busy_timeout_ms / 1000,
                               isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self) -> None:
        with self._init_lock, closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('queued','running','retry_wait','succeeded','failed','cancelled')),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                    max_attempts INTEGER NOT NULL CHECK(max_attempts BETWEEN 1 AND 10),
                    idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    result_json TEXT,
                    error_code TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1))
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at)")

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> DurableJob | None:
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
            result = json.loads(row["result_json"]) if row["result_json"] is not None else None
        except json.JSONDecodeError as exc:
            raise RuntimeError("Job store contains invalid JSON; refusing unsafe recovery.") from exc
        return DurableJob(
            job_id=row["job_id"], task_type=row["task_type"], payload=payload,
            status=row["status"], attempts=row["attempts"], max_attempts=row["max_attempts"],
            idempotency_key=row["idempotency_key"], created_at=row["created_at"],
            updated_at=row["updated_at"], started_at=row["started_at"],
            finished_at=row["finished_at"], result=result, error_code=row["error_code"],
            cancel_requested=bool(row["cancel_requested"]),
        )

    def create_job(self, task_type: str, payload: dict[str, Any], *,
                   idempotency_key: str | None = None, max_attempts: int = 3) -> DurableJob:
        task_type = _bounded_text(task_type, "task_type", 100)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object.")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10.")
        if idempotency_key is not None:
            idempotency_key = _bounded_text(idempotency_key, "idempotency_key", 200)
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must contain only finite JSON-compatible values.") from exc
        if len(payload_json.encode("utf-8")) > 64 * 1024:
            raise ValueError("payload exceeds the 64 KiB limit.")
        now = _now()
        job_id = str(uuid.uuid4())
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if idempotency_key:
                    prior = conn.execute("SELECT * FROM jobs WHERE idempotency_key=?",
                                         (idempotency_key,)).fetchone()
                    if prior:
                        if prior["task_type"] != task_type or prior["payload_json"] != payload_json:
                            raise IdempotencyConflict("Idempotency key already belongs to a different operation.")
                        conn.commit()
                        return self._decode(prior)
                conn.execute("""
                    INSERT INTO jobs(job_id,task_type,payload_json,status,attempts,max_attempts,
                        idempotency_key,created_at,updated_at)
                    VALUES(?,?,?,'queued',0,?,?,?,?)
                """,
                    (job_id, task_type, payload_json, max_attempts, idempotency_key, now, now))
                row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                conn.commit()
                return self._decode(row)
            except Exception:
                conn.rollback()
                raise

    def get_job(self, job_id: str) -> DurableJob | None:
        if not isinstance(job_id, str) or not job_id:
            return None
        with closing(self._connect()) as conn:
            return self._decode(conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())

    def list_jobs(self, *, limit: int = 100, statuses: set[str] | None = None) -> list[DurableJob]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000.")
        if statuses is not None and not statuses.issubset(ACTIVE_STATES | TERMINAL_STATES):
            raise ValueError("Unknown job status.")
        with closing(self._connect()) as conn:
            if statuses:
                ordered = sorted(statuses)
                placeholders = ",".join("?" for _ in ordered)
                rows = conn.execute(
                    f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY created_at DESC LIMIT ?",
                    (*ordered, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [self._decode(row) for row in rows]

    def claim_next(self) -> DurableJob | None:
        """Atomically claim one queued/retryable job, incrementing its attempt count."""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("""
                    SELECT * FROM jobs
                    WHERE status IN ('queued','retry_wait') AND cancel_requested=0
                      AND attempts < max_attempts
                    ORDER BY created_at ASC LIMIT 1
                """).fetchone()
                if row is None:
                    conn.commit()
                    return None
                now = _now()
                changed = conn.execute("""
                    UPDATE jobs SET status='running', attempts=attempts+1,
                        started_at=COALESCE(started_at,?), updated_at=?
                    WHERE job_id=? AND status IN ('queued','retry_wait') AND cancel_requested=0
                """, (now, now, row["job_id"])).rowcount
                if changed != 1:
                    conn.rollback()
                    return None
                updated = conn.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()
                conn.commit()
                return self._decode(updated)
            except Exception:
                conn.rollback()
                raise

    def finish(self, job_id: str, *, result: Any = None) -> DurableJob:
        try:
            result_json = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("result must be JSON-compatible.") from exc
        if len(result_json.encode("utf-8")) > 64 * 1024:
            raise ValueError("result exceeds the 64 KiB limit.")
        now = _now()
        with closing(self._connect()) as conn:
            changed = conn.execute("""
                UPDATE jobs SET status='succeeded',result_json=?,error_code=NULL,
                    finished_at=?,updated_at=?
                WHERE job_id=? AND status='running' AND cancel_requested=0
            """, (result_json, now, now, job_id)).rowcount
            if changed != 1:
                raise ValueError("Job is missing, not running, or cancellation was requested.")
            return self._decode(conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())

    def fail(self, job_id: str, *, error_code: str, retryable: bool = False) -> DurableJob:
        error_code = _bounded_text(error_code, "error_code", 100)
        now = _now()
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["status"] != "running":
                raise ValueError("Job is missing or not running.")
            if row["cancel_requested"]:
                status, finished_at = "cancelled", now
            elif retryable and row["attempts"] < row["max_attempts"]:
                status, finished_at = "retry_wait", None
            else:
                status, finished_at = "failed", now
            conn.execute("""
                UPDATE jobs SET status=?,error_code=?,finished_at=?,updated_at=?
                WHERE job_id=? AND status='running'
            """, (status, error_code, finished_at, now, job_id))
            return self._decode(conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())

    def request_cancel(self, job_id: str) -> bool:
        """Cancel queued jobs immediately; running handlers must poll cancellation_requested()."""
        now = _now()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["status"] in TERMINAL_STATES:
                conn.commit()
                return False
            if row["status"] in {"queued", "retry_wait"}:
                conn.execute("UPDATE jobs SET status='cancelled',cancel_requested=1,finished_at=?,updated_at=? WHERE job_id=?",
                             (now, now, job_id))
            else:
                conn.execute("UPDATE jobs SET cancel_requested=1,updated_at=? WHERE job_id=?", (now, job_id))
            conn.commit()
            return True

    def cancellation_requested(self, job_id: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return bool(row["cancel_requested"]) if row else True

    def recover_stale_running(self, *, older_than_seconds: int) -> dict[str, int]:
        """Requeue stale interrupted jobs or fail exhausted ones; never runs handlers itself."""
        if isinstance(older_than_seconds, bool) or not isinstance(older_than_seconds, int) or older_than_seconds < 1:
            raise ValueError("older_than_seconds must be a positive integer.")
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()
        now = _now()
        counts = {"requeued": 0, "failed": 0, "cancelled": 0}
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT * FROM jobs WHERE status='running' AND updated_at < ?", (cutoff,)).fetchall()
            for row in rows:
                if row["cancel_requested"]:
                    status = "cancelled"
                    counts["cancelled"] += 1
                    finished_at = now
                elif row["attempts"] < row["max_attempts"]:
                    status = "retry_wait"
                    counts["requeued"] += 1
                    finished_at = None
                else:
                    status = "failed"
                    counts["failed"] += 1
                    finished_at = now
                conn.execute("UPDATE jobs SET status=?,finished_at=?,updated_at=?,error_code=? WHERE job_id=?",
                             (status, finished_at, now, "worker_interrupted", row["job_id"]))
            conn.commit()
        return counts
