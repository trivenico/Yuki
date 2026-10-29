"""Durable local task ledger with explicit, validated lifecycle transitions.

This store tracks task metadata and status only. It does not execute tools or grant
permissions; callers must continue using the guarded runtime and approval gates.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid4


TASK_STATUSES = frozenset({
    "queued", "running", "awaiting_approval", "awaiting_verification", "completed", "failed", "cancelled",
})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_TRANSITIONS = {
    "queued": frozenset({"running", "awaiting_approval", "failed", "cancelled"}),
    "running": frozenset({"awaiting_approval", "awaiting_verification", "failed", "cancelled"}),
    "awaiting_approval": frozenset({"running", "failed", "cancelled"}),
    "awaiting_verification": frozenset({"completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
_MAX_TITLE = 240
_MAX_DETAIL = 4000
_MAX_RESULT = 8000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskLedger:
    """SQLite-backed task lifecycle store; no task execution occurs here."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with closing(self._connect()) as db:
            with db:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        detail TEXT NOT NULL DEFAULT '',
                        result TEXT NOT NULL DEFAULT '',
                        error_code TEXT NOT NULL DEFAULT '',
                        verification_evidence TEXT NOT NULL DEFAULT '',
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS tasks_updated_idx ON tasks(updated_at DESC)")
                columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
                if "verification_evidence" not in columns:
                    db.execute("ALTER TABLE tasks ADD COLUMN verification_evidence TEXT NOT NULL DEFAULT ''")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=3.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 3000")
        return db

    @staticmethod
    def _task(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    def create(self, title: str, *, detail: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > _MAX_TITLE:
            raise ValueError("Task title must be non-empty and at most 240 characters.")
        if not isinstance(detail, str) or len(detail) > _MAX_DETAIL:
            raise ValueError("Task detail must be text of at most 4000 characters.")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError("Task metadata must be an object.")
        try:
            metadata_json = json.dumps(metadata, ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Task metadata must be JSON-serializable.") from exc
        if len(metadata_json) > 8000:
            raise ValueError("Task metadata exceeds 8000 characters.")
        task_id = uuid4().hex
        now = _now()
        with self._lock, closing(self._connect()) as db:
            with db:
                db.execute(
                    "INSERT INTO tasks (id,title,status,created_at,updated_at,detail,metadata_json) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (task_id, title.strip(), "queued", now, now, detail, metadata_json),
                )
                row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task(row)

    def transition(
        self, task_id: str, status: str, *, result: str = "", error_code: str = "",
        verification_evidence: str = "",
    ) -> dict[str, Any]:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("Task ID must be non-empty.")
        if status not in TASK_STATUSES:
            raise ValueError("Unknown task status.")
        if not isinstance(result, str) or len(result) > _MAX_RESULT:
            raise ValueError("Task result must be text of at most 8000 characters.")
        if not isinstance(error_code, str) or len(error_code) > 80:
            raise ValueError("Error code must be text of at most 80 characters.")
        if not isinstance(verification_evidence, str) or len(verification_evidence) > 4000:
            raise ValueError("Verification evidence must be text of at most 4000 characters.")
        if status == "completed" and not verification_evidence.strip():
            raise ValueError("A task cannot be completed without verification evidence.")
        with self._lock, closing(self._connect()) as db:
            with db:
                # Acquire SQLite's write reservation before reading state. The Python
                # lock protects this instance only; other processes may share this DB.
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if row is None:
                    raise KeyError("Task not found.")
                current = row["status"]
                if status not in _TRANSITIONS[current]:
                    raise ValueError(f"Invalid task transition: {current} -> {status}.")
                db.execute(
                    "UPDATE tasks SET status=?,updated_at=?,result=?,error_code=?,verification_evidence=? WHERE id=?",
                    (status, _now(), result, error_code, verification_evidence.strip(), task_id),
                )
                updated = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task(updated)

    def record_verification_evidence(self, task_id: str, evidence: str) -> dict[str, Any]:
        """Record an observation without asserting that the task goal is complete."""
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("Task ID must be non-empty.")
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 4000:
            raise ValueError("Verification evidence must be non-empty text of at most 4000 characters.")
        with self._lock, closing(self._connect()) as db:
            with db:
                # Serialize the state check and update across independent processes.
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if row is None:
                    raise KeyError("Task not found.")
                if row["status"] != "awaiting_verification":
                    raise ValueError("Evidence can only be recorded for tasks awaiting verification.")
                db.execute(
                    "UPDATE tasks SET updated_at=?,verification_evidence=? WHERE id=?",
                    (_now(), evidence.strip(), task_id),
                )
                updated = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task(updated)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock, closing(self._connect()) as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task(row) if row else None

    def list(self, *, limit: int = 50, include_terminal: bool = True) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("Task list limit must be between 1 and 200.")
        sql = "SELECT * FROM tasks"
        params: list[Any] = []
        if not include_terminal:
            placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
            sql += f" WHERE status NOT IN ({placeholders})"
            params.extend(sorted(TERMINAL_STATUSES))
        sql += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, closing(self._connect()) as db:
            rows = db.execute(sql, params).fetchall()
        return [self._task(row) for row in rows]

    def cancel(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id)
        if task is None:
            raise KeyError("Task not found.")
        return self.transition(task_id, "cancelled")
