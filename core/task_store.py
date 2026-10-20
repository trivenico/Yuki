"""Durable task lifecycle ledger.

Interrupted work is recorded for operator review and is never replayed
automatically: the store cannot know whether an external side effect completed
before the process stopped.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_TERMINAL = {"completed", "failed", "interrupted"}
_ALLOWED = {
    "queued": {"running", "interrupted", "failed"},
    "running": {"completed", "failed", "interrupted"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    """SQLite-backed lifecycle history with atomic, validated state transitions."""

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 2000) -> None:
        if isinstance(busy_timeout_ms, bool) or not isinstance(busy_timeout_ms, int):
            raise ValueError("busy_timeout_ms must be an integer")
        if not 1 <= busy_timeout_ms <= 30000:
            raise ValueError("busy_timeout_ms must be between 1 and 30000")
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = busy_timeout_ms
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'running', 'completed', 'failed', 'interrupted')
                    ),
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_status_updated ON tasks(status, updated_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = ?", (self.busy_timeout_ms,))
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @staticmethod
    def _record(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        if result["result_json"] is not None:
            try:
                result["result"] = json.loads(result.pop("result_json"))
            except (TypeError, json.JSONDecodeError):
                result["result"] = None
                result.pop("result_json", None)
        else:
            result.pop("result_json", None)
            result["result"] = None
        return result

    def create(self, goal: str) -> dict[str, Any]:
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("task goal must be a non-empty string")
        if len(goal) > 4000:
            raise ValueError("task goal exceeds 4000 characters")
        task_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO tasks(task_id, goal, status, created_at, updated_at) VALUES (?, ?, 'queued', ?, ?)",
                (task_id, goal.strip(), now, now),
            )
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        record = self._record(row)
        assert record is not None
        return record

    def transition(
        self, task_id: str, status: str, *, result: Any = None
    ) -> dict[str, Any]:
        if status not in _TERMINAL | {"running"}:
            raise ValueError("unsupported task status")
        result_json = None
        if result is not None:
            try:
                result_json = json.dumps(result, ensure_ascii=True, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("task result must be JSON-serializable") from exc
            if len(result_json) > 16000:
                raise ValueError("task result exceeds storage limit")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("task not found")
            current = row["status"]
            if status not in _ALLOWED.get(current, set()):
                raise ValueError(f"invalid task transition: {current} -> {status}")
            connection.execute(
                "UPDATE tasks SET status = ?, result_json = ?, updated_at = ? WHERE task_id = ?",
                (status, result_json, _now(), task_id),
            )
            updated = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        record = self._record(updated)
        assert record is not None
        return record

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._record(row)

    def list_tasks(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [record for row in rows if (record := self._record(row)) is not None]

    def recover_interrupted(self) -> int:
        """Mark unfinished tasks interrupted; never re-run possibly side-effecting work."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status = 'interrupted', updated_at = ? "
                "WHERE status IN ('queued', 'running')",
                (_now(),),
            )
            return cursor.rowcount
