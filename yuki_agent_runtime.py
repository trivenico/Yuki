"""Durable, policy-bounded task storage for Yuki's agent runtime.

This module schedules metadata only. It does not execute model prompts, tools, or
arbitrary code. A separate worker must explicitly claim a task and enforce its
own tool permissions and sandbox boundaries.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


STATUSES = frozenset({"queued", "running", "succeeded", "failed", "cancelled"})
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime | None = None) -> str:
    value = value or _now()
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _positive_int(value: int, field: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise ValueError(f"{field} must be an integer between 1 and {maximum}")
    return value


class TaskStore:
    """SQLite-backed task queue with leases, approvals, budgets and an event ledger."""

    def __init__(self, database: str | Path, *, busy_timeout_ms: int = 5000):
        raw_database = str(database)
        if not raw_database.strip():
            raise ValueError("database path must be non-empty")
        if raw_database == ":memory:":
            raise ValueError("TaskStore requires a persistent database path, not :memory:")
        self.database = str(Path(raw_database).expanduser().resolve())
        self.busy_timeout_ms = _positive_int(busy_timeout_ms, "busy_timeout_ms", maximum=120000)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=self.busy_timeout_ms / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return connection

    @contextmanager
    def _session(self):
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _initialize(self):
        with self._session() as db:
            # Rollback journal is more portable for a single-host SQLite queue,
            # especially on Windows where WAL sidecar handles can impede temp cleanup.
            db.execute("PRAGMA journal_mode = DELETE")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE,
                    task_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('queued','running','succeeded','failed','cancelled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deadline_at TEXT,
                    max_attempts INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    budget_tokens INTEGER,
                    budget_seconds INTEGER,
                    approval_required INTEGER NOT NULL DEFAULT 0,
                    approved INTEGER NOT NULL DEFAULT 0,
                    worker_id TEXT,
                    lease_expires_at TEXT,
                    result_json TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_tasks_claim
                    ON agent_tasks(status, approved, created_at);
                CREATE TABLE IF NOT EXISTS agent_task_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES agent_tasks(id),
                    at TEXT NOT NULL,
                    event TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
            """)

    @staticmethod
    def _event(db, task_id: str, event: str, details: dict[str, Any] | None = None):
        db.execute(
            "INSERT INTO agent_task_events(task_id, at, event, details_json) VALUES(?,?,?,?)",
            (task_id, _stamp(), event, json.dumps(details or {}, ensure_ascii=False, sort_keys=True)),
        )

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        result_json = item.pop("result_json")
        item["result"] = json.loads(result_json) if result_json is not None else None
        item["approval_required"] = bool(item["approval_required"])
        item["approved"] = bool(item["approved"])
        return item

    def submit(
        self,
        task_type: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        deadline_seconds: int | None = None,
        max_attempts: int = 3,
        budget_tokens: int | None = None,
        budget_seconds: int | None = None,
        approval_required: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(task_type, str) or not task_type.strip() or len(task_type.strip()) > 120:
            raise ValueError("task_type must be non-empty text of at most 120 characters")
        if not isinstance(payload, dict):
            raise TypeError("payload must be a JSON object")
        if not isinstance(approval_required, bool):
            raise TypeError("approval_required must be a boolean")
        max_attempts = _positive_int(max_attempts, "max_attempts", maximum=20)
        if deadline_seconds is not None:
            deadline_seconds = _positive_int(deadline_seconds, "deadline_seconds", maximum=604800)
        if budget_tokens is not None:
            budget_tokens = _positive_int(budget_tokens, "budget_tokens", maximum=100000000)
        if budget_seconds is not None:
            budget_seconds = _positive_int(budget_seconds, "budget_seconds", maximum=604800)
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200
        ):
            raise ValueError("idempotency_key must be non-empty text of at most 200 characters")
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must contain only finite JSON-compatible values") from exc

        now = _now()
        created = _stamp(now)
        deadline = _stamp(now + timedelta(seconds=deadline_seconds)) if deadline_seconds else None
        task_id = str(uuid.uuid4())
        with self._session() as db:
            db.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                existing = db.execute(
                    "SELECT * FROM agent_tasks WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if existing:
                    return self._decode(existing)
            db.execute("""
                INSERT INTO agent_tasks(
                    id,idempotency_key,task_type,payload_json,status,created_at,updated_at,
                    deadline_at,max_attempts,budget_tokens,budget_seconds,approval_required,approved
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                task_id, idempotency_key, task_type.strip(), payload_json, "queued",
                created, created, deadline, max_attempts, budget_tokens, budget_seconds,
                int(approval_required), int(not approval_required),
            ))
            self._event(db, task_id, "submitted", {
                "approval_required": approval_required, "max_attempts": max_attempts,
            })
            return self._decode(db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone())

    def approve(self, task_id: str, *, approved_by: str) -> dict[str, Any]:
        if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 200:
            raise ValueError("approved_by must be non-empty text of at most 200 characters")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("task not found")
            if row["status"] != "queued":
                raise ValueError("only queued tasks can be approved")
            db.execute("UPDATE agent_tasks SET approved=1,updated_at=? WHERE id=?", (_stamp(), task_id))
            self._event(db, task_id, "approved", {"approved_by": approved_by.strip()})
            return self._decode(db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone())

    def claim_next(self, worker_id: str, *, lease_seconds: int = 60) -> dict[str, Any] | None:
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200:
            raise ValueError("worker_id must be non-empty text of at most 200 characters")
        lease_seconds = _positive_int(lease_seconds, "lease_seconds", maximum=3600)
        now = _now()
        now_text = _stamp(now)
        lease = _stamp(now + timedelta(seconds=lease_seconds))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Reclaim expired leases. A task is requeued only if its retry budget remains.
            expired = db.execute(
                "SELECT * FROM agent_tasks WHERE status='running' AND lease_expires_at <= ?",
                (now_text,),
            ).fetchall()
            for row in expired:
                status = "queued" if row["attempts"] < row["max_attempts"] else "failed"
                if row["deadline_at"] and row["deadline_at"] <= now_text:
                    status = "failed"
                db.execute("""
                    UPDATE agent_tasks SET status=?,worker_id=NULL,lease_expires_at=NULL,
                        updated_at=?,error=? WHERE id=?
                """, (status, now_text, "worker lease expired", row["id"]))
                self._event(db, row["id"], "lease_expired", {"next_status": status})
            # Expired queued jobs are failed before claiming.
            overdue = db.execute(
                "SELECT id FROM agent_tasks WHERE status='queued' AND deadline_at IS NOT NULL AND deadline_at <= ?",
                (now_text,),
            ).fetchall()
            for row in overdue:
                db.execute(
                    "UPDATE agent_tasks SET status='failed',updated_at=?,error=? WHERE id=?",
                    (now_text, "task deadline expired before execution", row["id"]),
                )
                self._event(db, row["id"], "deadline_expired")
            row = db.execute("""
                SELECT * FROM agent_tasks
                WHERE status='queued' AND approved=1
                  AND (deadline_at IS NULL OR deadline_at > ?)
                  AND attempts < max_attempts
                ORDER BY created_at, id LIMIT 1
            """, (now_text,)).fetchone()
            if row is None:
                return None
            db.execute("""
                UPDATE agent_tasks SET status='running',attempts=attempts+1,worker_id=?,
                    lease_expires_at=?,updated_at=?,error=NULL WHERE id=?
            """, (worker_id.strip(), lease, now_text, row["id"]))
            self._event(db, row["id"], "claimed", {"worker_id": worker_id.strip(), "attempt": row["attempts"] + 1})
            return self._decode(db.execute("SELECT * FROM agent_tasks WHERE id=?", (row["id"],)).fetchone())

    def heartbeat(self, task_id: str, worker_id: str, *, lease_seconds: int = 60) -> dict[str, Any]:
        lease_seconds = _positive_int(lease_seconds, "lease_seconds", maximum=3600)
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be non-empty text")
        now = _stamp()
        lease = _stamp(_now() + timedelta(seconds=lease_seconds))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("task not found")
            if row["status"] != "running" or row["worker_id"] != worker_id.strip():
                raise ValueError("task is not owned by this running worker")
            if row["deadline_at"] and row["deadline_at"] <= now:
                db.execute("UPDATE agent_tasks SET status='failed',updated_at=?,error=?,worker_id=NULL,lease_expires_at=NULL WHERE id=?",
                           (now, "task deadline expired", task_id))
                self._event(db, task_id, "deadline_expired")
                return self._decode(db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone())
            db.execute("UPDATE agent_tasks SET lease_expires_at=?,updated_at=? WHERE id=?", (lease, now, task_id))
            self._event(db, task_id, "heartbeat", {"worker_id": worker_id.strip()})
            return self._decode(db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone())

    def complete(self, task_id: str, worker_id: str, result: Any) -> dict[str, Any]:
        try:
            result_json = json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("result must be JSON-compatible and finite") from exc
        return self._finish(task_id, worker_id, "succeeded", result_json=result_json)

    def fail(self, task_id: str, worker_id: str, error: str, *, retryable: bool = True) -> dict[str, Any]:
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must be non-empty text")
        if not isinstance(retryable, bool):
            raise TypeError("retryable must be a boolean")
        return self._finish(task_id, worker_id, "failed", error=error.strip()[:1000], retryable=retryable)

    def _finish(self, task_id, worker_id, status, *, result_json=None, error=None, retryable=False):
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be non-empty text")
        now = _stamp()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("task not found")
            if row["status"] != "running" or row["worker_id"] != worker_id.strip():
                raise ValueError("task is not owned by this running worker")
            if row["deadline_at"] and row["deadline_at"] <= now:
                status, error, result_json = "failed", "task deadline expired", None
                retryable = False
            next_status = "queued" if status == "failed" and retryable and row["attempts"] < row["max_attempts"] and not (
                row["deadline_at"] and row["deadline_at"] <= now
            ) else status
            db.execute("""
                UPDATE agent_tasks SET status=?,updated_at=?,worker_id=NULL,lease_expires_at=NULL,
                    result_json=?,error=? WHERE id=?
            """, (next_status, now, result_json if next_status == "succeeded" else None,
                  error if next_status == "failed" else None, task_id))
            self._event(db, task_id, "retry_scheduled" if next_status == "queued" else next_status,
                        {"retryable": retryable} if status == "failed" else {})
            return self._decode(db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone())

    def cancel(self, task_id: str, *, requested_by: str) -> dict[str, Any]:
        if not isinstance(requested_by, str) or not requested_by.strip():
            raise ValueError("requested_by must be non-empty text")
        now = _stamp()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("task not found")
            if row["status"] in TERMINAL:
                return self._decode(row)
            db.execute("""
                UPDATE agent_tasks SET status='cancelled',updated_at=?,worker_id=NULL,
                    lease_expires_at=NULL,error=? WHERE id=?
            """, (now, "cancelled by request", task_id))
            self._event(db, task_id, "cancelled", {"requested_by": requested_by.strip()[:200]})
            return self._decode(db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone())

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            return self._decode(db.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone())

    def list_tasks(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = _positive_int(limit, "limit", maximum=1000)
        if status is not None and status not in STATUSES:
            raise ValueError("invalid task status")
        with self._connect() as db:
            if status:
                rows = db.execute(
                    "SELECT * FROM agent_tasks WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM agent_tasks ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            return [self._decode(row) for row in rows]

    def events(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT sequence,task_id,at,event,details_json FROM agent_task_events WHERE task_id=? ORDER BY sequence",
                (task_id,),
            ).fetchall()
            return [
                {**dict(row), "details": json.loads(row["details_json"])}
                for row in rows
            ]
