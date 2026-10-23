"""SQLite-backed task checkpoints for safe, explicit task resumption.

This store preserves task intent and progress; it does not itself execute tasks or
make external side effects exactly-once. Resume callers must reconcile uncertain
side effects before retrying them.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

_TASK_STATES = frozenset({
    "pending", "running", "paused", "completed", "failed", "needs_reconciliation",
})
_TERMINAL_STATES = frozenset({"completed", "failed"})


@dataclass(frozen=True)
class TaskCheckpoint:
    task_id: str
    revision: int
    status: str
    payload: dict[str, Any]
    created_at: str
    updated_at: str


class SQLiteTaskCheckpointStore:
    """Persist versioned task progress with optimistic concurrency protection."""

    def __init__(self, database_path: str, *, timeout_seconds: float = 5.0) -> None:
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if type(timeout_seconds) not in (int, float) or not 0.1 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 0.1 and 60.")
        self.database_path = str(Path(database_path).expanduser())
        self.timeout_seconds = float(timeout_seconds)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS task_checkpoints (
                    task_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    status TEXT NOT NULL CHECK (status IN
                        ('pending','running','paused','completed','failed','needs_reconciliation')),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_checkpoints_status "
                "ON task_checkpoints(status, updated_at)"
            )

    def _connect(self):
        db = sqlite3.connect(
            self.database_path, timeout=self.timeout_seconds, isolation_level=None
        )
        db.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        return db

    @staticmethod
    def _validate_task_id(task_id: str) -> None:
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
            raise ValueError("task_id must be non-empty text of at most 200 characters.")

    @staticmethod
    def _encode_payload(payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object.")
        try:
            encoded = json.dumps(
                payload, ensure_ascii=False, allow_nan=False, sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must contain only JSON-safe values.") from exc
        if len(encoded.encode("utf-8")) > 256 * 1024:
            raise ValueError("payload exceeds 256 KiB.")
        return encoded

    @staticmethod
    def _from_row(row) -> TaskCheckpoint:
        return TaskCheckpoint(
            task_id=row[0], revision=row[1], status=row[2],
            payload=json.loads(row[3]), created_at=row[4], updated_at=row[5],
        )

    def create(self, task_id: str, payload: dict[str, Any], *, status: str = "pending") -> TaskCheckpoint:
        self._validate_task_id(task_id)
        if status not in _TASK_STATES:
            raise ValueError("Unsupported task status.")
        encoded = self._encode_payload(payload)
        now = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        try:
            db.execute(
                "INSERT INTO task_checkpoints "
                "(task_id, revision, status, payload_json, created_at, updated_at) "
                "VALUES (?, 1, ?, ?, ?, ?)",
                (task_id, status, encoded, now, now),
            )
            return TaskCheckpoint(task_id, 1, status, json.loads(encoded), now, now)
        except sqlite3.IntegrityError as exc:
            raise ValueError("task_id already exists; checkpoints are not overwritten.") from exc
        finally:
            db.close()

    def get(self, task_id: str) -> TaskCheckpoint | None:
        self._validate_task_id(task_id)
        db = self._connect()
        try:
            row = db.execute(
                "SELECT task_id, revision, status, payload_json, created_at, updated_at "
                "FROM task_checkpoints WHERE task_id = ?", (task_id,),
            ).fetchone()
            return self._from_row(row) if row else None
        finally:
            db.close()

    def checkpoint(
        self, task_id: str, *, expected_revision: int, payload: dict[str, Any],
        status: str,
    ) -> TaskCheckpoint:
        """Advance a checkpoint only if its revision matches the caller's snapshot."""
        self._validate_task_id(task_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer.")
        if status not in _TASK_STATES:
            raise ValueError("Unsupported task status.")
        encoded = self._encode_payload(payload)
        now = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision, status, created_at FROM task_checkpoints WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError("Task checkpoint does not exist.")
            revision, old_status, created_at = row
            if revision != expected_revision:
                raise RuntimeError("Stale task checkpoint revision; reload before updating.")
            if old_status in _TERMINAL_STATES:
                raise ValueError("Terminal task checkpoints cannot be resumed or changed.")
            if old_status == "needs_reconciliation" and status == "running":
                raise ValueError("Reconcile uncertain side effects before resuming execution.")
            next_revision = revision + 1
            db.execute(
                "UPDATE task_checkpoints SET revision = ?, status = ?, payload_json = ?, updated_at = ? "
                "WHERE task_id = ? AND revision = ?",
                (next_revision, status, encoded, now, task_id, expected_revision),
            )
            db.execute("COMMIT")
            return TaskCheckpoint(task_id, next_revision, status, json.loads(encoded), created_at, now)
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def list_incomplete(self, *, limit: int = 100) -> list[TaskCheckpoint]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000.")
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT task_id, revision, status, payload_json, created_at, updated_at "
                "FROM task_checkpoints WHERE status NOT IN ('completed','failed') "
                "ORDER BY updated_at ASC LIMIT ?", (limit,),
            ).fetchall()
            return [self._from_row(row) for row in rows]
        finally:
            db.close()
