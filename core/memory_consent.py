"""Persistent per-user memory consent, stored separately from memory content."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

from core.sqlite_file_security import prepare_private_sqlite_path


_USER_ID = re.compile(r"^[^\x00-\x1f\x7f]{1,512}$")


class MemoryConsentStore:
    """SQLite-backed explicit opt-in state keyed by verified auth user IDs."""

    def __init__(self, database_path: str | Path):
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connection() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_consent (
                    user_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                    updated_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _validate_user_id(user_id: str) -> str:
        if not isinstance(user_id, str) or not _USER_ID.fullmatch(user_id.strip()):
            raise ValueError("verified user_id is required")
        return user_id.strip()

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def enabled(self, user_id: str) -> bool:
        owner = self._validate_user_id(user_id)
        with self._connection() as db:
            row = db.execute(
                "SELECT enabled FROM memory_consent WHERE user_id=?", (owner,)
            ).fetchone()
        return bool(row[0]) if row else False

    def set_enabled(self, user_id: str, enabled: bool) -> dict:
        owner = self._validate_user_id(user_id)
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO memory_consent(user_id, enabled, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled=excluded.enabled, updated_at=excluded.updated_at
                """,
                (owner, int(enabled), updated_at),
            )
        return {"enabled": enabled, "updated_at": updated_at}
