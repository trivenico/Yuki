"""Durable task lifecycle state store.

This module records intent and execution state; it does not execute tasks. A process
crash after an external side effect may require reconciliation, so interrupted running
tasks are marked recovery_required instead of being blindly retried.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any


TASK_STATES = frozenset({
    "pending", "approved", "running", "completed", "failed",
    "cancelled", "recovery_required",
})
_ALLOWED_TRANSITIONS = {
    "pending": frozenset({"approved", "cancelled", "failed"}),
    "approved": frozenset({"running", "cancelled", "failed"}),
    "running": frozenset({"completed", "failed", "recovery_required"}),
    "recovery_required": frozenset({"approved", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
_MAX_JSON_BYTES = 64 * 1024


class TaskLifecycleError(RuntimeError):
    """Base exception for invalid or conflicting lifecycle operations."""


class InvalidTransition(TaskLifecycleError):
    """Raised when a requested task state transition is not permitted."""


class TaskConflict(TaskLifecycleError):
    """Raised when a task version or idempotency key conflicts."""


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    idempotency_key: str
    task_type: str
    payload: dict[str, Any]
    state: str
    result: Any
    error_code: str | None
    created_at: str
    updated_at: str
    version: int


class SQLiteTaskLifecycleStore:
    """SQLite-backed task records with atomic, versioned state transitions."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS task_lifecycle (
                    task_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    task_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_task_lifecycle_state ON task_lifecycle(state, updated_at)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS task_lifecycle_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_task_lifecycle_events_task ON task_lifecycle_events(task_id, event_id)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout = 5000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _json(value: Any, field: str) -> str:
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be JSON serializable without NaN or infinity.") from exc
        if len(encoded.encode("utf-8")) > _MAX_JSON_BYTES:
            raise ValueError(f"{field} exceeds {_MAX_JSON_BYTES} bytes.")
        return encoded

    @staticmethod
    def _record(row) -> TaskRecord:
        return TaskRecord(
            task_id=row[0], idempotency_key=row[1], task_type=row[2],
            payload=json.loads(row[3]), state=row[4],
            result=json.loads(row[5]) if row[5] is not None else None,
            error_code=row[6], created_at=row[7], updated_at=row[8], version=row[9],
        )

    @staticmethod
    def _append_event(db, *, task_id: str, event_type: str, from_state: str | None,
                      to_state: str, error_code: str | None, created_at: str) -> None:
        previous = db.execute(
            "SELECT event_hash FROM task_lifecycle_events ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous[0] if previous else "0" * 64
        event = {
            "task_id": task_id, "event_type": event_type, "from_state": from_state,
            "to_state": to_state, "error_code": error_code, "created_at": created_at,
            "previous_hash": previous_hash,
        }
        event_hash = hashlib.sha256(
            json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        db.execute(
            """INSERT INTO task_lifecycle_events
               (task_id, event_type, from_state, to_state, error_code, created_at, previous_hash, event_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, event_type, from_state, to_state, error_code, created_at, previous_hash, event_hash),
        )

    def list_events(self, *, task_id: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000.")
        if task_id is not None and (not isinstance(task_id, str) or not task_id):
            raise ValueError("task_id must be a non-empty string.")
        sql = """SELECT event_id, task_id, event_type, from_state, to_state, error_code,
                        created_at, previous_hash, event_hash
                 FROM task_lifecycle_events"""
        if task_id is not None:
            sql += " WHERE task_id = ? ORDER BY event_id LIMIT ?"
            params = (task_id, limit)
        else:
            sql += " ORDER BY event_id LIMIT ?"
            params = (limit,)
        with self._connect() as db:
            rows = db.execute(sql, params).fetchall()
        keys = ("event_id", "task_id", "event_type", "from_state", "to_state",
                "error_code", "created_at", "previous_hash", "event_hash")
        return [dict(zip(keys, row)) for row in rows]

    def verify_audit_chain(self) -> bool:
        """Verify the global append-order hash chain; this detects edits/deletions."""
        with self._connect() as db:
            rows = db.execute(
                """SELECT task_id, event_type, from_state, to_state, error_code,
                          created_at, previous_hash, event_hash
                   FROM task_lifecycle_events ORDER BY event_id"""
            ).fetchall()
        previous_hash = "0" * 64
        for row in rows:
            task_id, event_type, from_state, to_state, error_code, created_at, stored_previous, stored_hash = row
            if stored_previous != previous_hash:
                return False
            event = {
                "task_id": task_id, "event_type": event_type, "from_state": from_state,
                "to_state": to_state, "error_code": error_code, "created_at": created_at,
                "previous_hash": stored_previous,
            }
            expected_hash = hashlib.sha256(
                json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            if stored_hash != expected_hash:
                return False
            previous_hash = stored_hash
        return True

    def create_task(self, *, idempotency_key: str, task_type: str,
                    payload: dict[str, Any]) -> TaskRecord:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("idempotency_key must be a non-empty string of at most 200 characters.")
        if not isinstance(task_type, str) or not task_type.strip() or len(task_type) > 120:
            raise ValueError("task_type must be a non-empty string of at most 120 characters.")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object.")
        payload_json = self._json(payload, "payload")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute(
                    "SELECT task_id, idempotency_key, task_type, payload_json, state, result_json, error_code, created_at, updated_at, version FROM task_lifecycle WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    if existing[2] != task_type or existing[3] != payload_json:
                        raise TaskConflict("idempotency_key was already used for a different task payload.")
                    db.execute("COMMIT")
                    return self._record(existing)
                task_id = secrets.token_urlsafe(24)
                db.execute(
                    """INSERT INTO task_lifecycle
                       (task_id, idempotency_key, task_type, payload_json, state, created_at, updated_at, version)
                       VALUES (?, ?, ?, ?, 'pending', ?, ?, 0)""",
                    (task_id, idempotency_key, task_type, payload_json, now, now),
                )
                self._append_event(
                    db, task_id=task_id, event_type="created", from_state=None,
                    to_state="pending", error_code=None, created_at=now,
                )
                row = db.execute(
                    "SELECT task_id, idempotency_key, task_type, payload_json, state, result_json, error_code, created_at, updated_at, version FROM task_lifecycle WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                db.execute("COMMIT")
                return self._record(row)
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def get_task(self, task_id: str) -> TaskRecord | None:
        if not isinstance(task_id, str) or not task_id:
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT task_id, idempotency_key, task_type, payload_json, state, result_json, error_code, created_at, updated_at, version FROM task_lifecycle WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._record(row) if row else None

    def transition(self, task_id: str, *, expected_state: str, new_state: str,
                   expected_version: int | None = None, result: Any = None,
                   error_code: str | None = None) -> TaskRecord:
        if expected_state not in TASK_STATES or new_state not in TASK_STATES:
            raise ValueError("Unknown task state.")
        if new_state not in _ALLOWED_TRANSITIONS[expected_state]:
            raise InvalidTransition(f"Transition {expected_state!r} -> {new_state!r} is not allowed.")
        result_json = self._json(result, "result") if result is not None else None
        if error_code is not None and (not isinstance(error_code, str) or not error_code or len(error_code) > 120):
            raise ValueError("error_code must be a non-empty string of at most 120 characters.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT task_id, idempotency_key, task_type, payload_json, state, result_json, error_code, created_at, updated_at, version FROM task_lifecycle WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                if row is None:
                    raise KeyError("Task not found.")
                if row[4] != expected_state or (expected_version is not None and row[9] != expected_version):
                    raise TaskConflict("Task state or version changed; reload before retrying.")
                if new_state == "completed" and result_json is None:
                    raise ValueError("completed tasks require a JSON result.")
                if new_state == "failed" and error_code is None:
                    raise ValueError("failed tasks require a bounded error_code.")
                db.execute(
                    """UPDATE task_lifecycle SET state = ?, result_json = ?, error_code = ?,
                       updated_at = ?, version = version + 1 WHERE task_id = ? AND state = ? AND version = ?""",
                    (new_state, result_json, error_code, now, task_id, expected_state, row[9]),
                )
                self._append_event(
                    db, task_id=task_id, event_type="state_transition",
                    from_state=expected_state, to_state=new_state,
                    error_code=error_code, created_at=now,
                )
                updated = db.execute(
                    "SELECT task_id, idempotency_key, task_type, payload_json, state, result_json, error_code, created_at, updated_at, version FROM task_lifecycle WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                db.execute("COMMIT")
                return self._record(updated)
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def mark_interrupted_tasks_for_recovery(self) -> int:
        """Mark tasks left running after a process restart; never auto-reexecute them."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                running = db.execute(
                    "SELECT task_id FROM task_lifecycle WHERE state = 'running'"
                ).fetchall()
                for (task_id,) in running:
                    db.execute(
                        """UPDATE task_lifecycle SET state = 'recovery_required',
                           error_code = 'interrupted_after_restart', updated_at = ?, version = version + 1
                           WHERE task_id = ? AND state = 'running'""",
                        (now, task_id),
                    )
                    self._append_event(
                        db, task_id=task_id, event_type="recovery_required",
                        from_state="running", to_state="recovery_required",
                        error_code="interrupted_after_restart", created_at=now,
                    )
                db.execute("COMMIT")
                return len(running)
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def list_tasks(self, *, states: tuple[str, ...] | None = None, limit: int = 100) -> list[TaskRecord]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000.")
        if states is not None:
            if not states or any(state not in TASK_STATES for state in states):
                raise ValueError("states must contain known task states.")
            placeholders = ",".join("?" for _ in states)
            query = f"SELECT task_id, idempotency_key, task_type, payload_json, state, result_json, error_code, created_at, updated_at, version FROM task_lifecycle WHERE state IN ({placeholders}) ORDER BY created_at LIMIT ?"
            params = (*states, limit)
        else:
            query = "SELECT task_id, idempotency_key, task_type, payload_json, state, result_json, error_code, created_at, updated_at, version FROM task_lifecycle ORDER BY created_at LIMIT ?"
            params = (limit,)
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [self._record(row) for row in rows]
