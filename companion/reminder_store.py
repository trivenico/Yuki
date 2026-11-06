"""Owner-scoped durable reminders for the Yuki companion.

Reminders are inert data: this store never executes commands or arbitrary actions.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4
from typing import Callable


class CompanionReminderStore:
    MAX_TEXT = 1000
    MAX_PER_OWNER = 1000

    def __init__(self, database_path: str | Path, *, authorize: Callable[[str, str], bool]):
        if not callable(authorize):
            raise TypeError("authorize must be callable")
        self.database_path = str(Path(database_path).expanduser().resolve())
        self.authorize = authorize
        self._lock = RLock()
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS companion_reminders (
                    reminder_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','cancelled','delivered')),
                    created_at TEXT NOT NULL
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_companion_reminders_owner_due "
                "ON companion_reminders(owner_id, status, due_at)"
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _check(self, owner_id: str, action: str) -> None:
        if not isinstance(owner_id, str) or not owner_id or not self.authorize(owner_id, action):
            raise PermissionError("Reminder access denied.")

    @staticmethod
    def _serialize(row) -> dict:
        return {key: row[key] for key in (
            "reminder_id", "text", "due_at", "status", "created_at"
        )}

    def create(self, *, owner_id: str, text: str, due_at: datetime) -> dict:
        self._check(owner_id, "create")
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > self.MAX_TEXT:
            raise ValueError("invalid_reminder_text")
        if not isinstance(due_at, datetime) or due_at.tzinfo is None or due_at.utcoffset() is None:
            raise ValueError("reminder_due_at_must_be_timezone_aware")
        due = due_at.astimezone(timezone.utc).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        reminder_id = uuid4().hex
        with self._lock, self._connect() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM companion_reminders WHERE owner_id=? AND status='pending'",
                (owner_id,),
            ).fetchone()[0]
            if count >= self.MAX_PER_OWNER:
                raise ValueError("reminder_limit_reached")
            db.execute(
                "INSERT INTO companion_reminders VALUES (?,?,?,?,?,?)",
                (reminder_id, owner_id, text.strip(), due, "pending", now),
            )
            row = db.execute(
                "SELECT * FROM companion_reminders WHERE reminder_id=?", (reminder_id,)
            ).fetchone()
            return self._serialize(row)

    def list(self, *, owner_id: str, include_inactive: bool = False, limit: int = 100) -> list[dict]:
        self._check(owner_id, "read")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise ValueError("invalid_reminder_limit")
        sql = "SELECT * FROM companion_reminders WHERE owner_id=?"
        params: list[object] = [owner_id]
        if not include_inactive:
            sql += " AND status='pending'"
        sql += " ORDER BY due_at, reminder_id LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as db:
            return [self._serialize(row) for row in db.execute(sql, params).fetchall()]

    def cancel(self, *, owner_id: str, reminder_id: str) -> bool:
        self._check(owner_id, "cancel")
        if not isinstance(reminder_id, str) or len(reminder_id) != 32:
            raise ValueError("invalid_reminder_id")
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "UPDATE companion_reminders SET status='cancelled' "
                "WHERE reminder_id=? AND owner_id=? AND status='pending'",
                (reminder_id, owner_id),
            )
            return cursor.rowcount == 1
