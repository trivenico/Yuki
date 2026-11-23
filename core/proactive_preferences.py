"""Owner-scoped proactive notification preferences; disabled by default."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path


class ProactiveNotificationPreferences:
    def __init__(self, path: str | Path):
        raw = Path(path).expanduser()
        if raw.is_symlink():
            raise ValueError("preference database must not be a symlink.")
        raw.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = raw.parent.resolve() / raw.name
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise ValueError("preference database must be a regular file.")
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS proactive_notification_preferences (
                    owner_id TEXT PRIMARY KEY,
                    notifications_enabled INTEGER NOT NULL
                        CHECK(notifications_enabled IN (0, 1)),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _owner(owner_id):
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200 or "\\x00" in owner_id:
            raise ValueError("owner_id must be bounded non-empty text.")
        return owner_id.strip()

    def get(self, *, owner_id: str) -> dict:
        owner_id = self._owner(owner_id)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            row = db.execute(
                "SELECT notifications_enabled, updated_at FROM proactive_notification_preferences WHERE owner_id = ?",
                (owner_id,),
            ).fetchone()
        return {
            "notifications_enabled": bool(row[0]) if row else False,
            "updated_at": row[1] if row else None,
        }

    def set(self, *, owner_id: str, notifications_enabled: bool) -> dict:
        owner_id = self._owner(owner_id)
        if type(notifications_enabled) is not bool:
            raise ValueError("notifications_enabled must be boolean.")
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                INSERT INTO proactive_notification_preferences
                    (owner_id, notifications_enabled, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(owner_id) DO UPDATE SET
                    notifications_enabled = excluded.notifications_enabled,
                    updated_at = CURRENT_TIMESTAMP
            """, (owner_id, int(notifications_enabled)))
            db.commit()
        return self.get(owner_id=owner_id)
