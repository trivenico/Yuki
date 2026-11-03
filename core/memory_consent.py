"""Persistent per-user memory consent, stored separately from memory content."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone


class MemoryConsentStore:
    """SQLite-backed explicit opt-in state keyed by verified auth user IDs."""

    def __init__(self, database_path: str | Path):
        path = Path(database_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = str(path)
        with self._connection() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS memory_consent (
                    user_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                    updated_at TEXT NOT NULL
                )
            """)

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    @contextmanager
    def _connection(self):
        """Commit or roll back work and always close the SQLite connection."""
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def _connection(self):
        """Commit or roll back work and always release the SQLite file handle."""
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def enabled(self, user_id: str) -> bool:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("verified user_id is required")
        with self._connection() as db:
            row = db.execute(
                "SELECT enabled FROM memory_consent WHERE user_id=?", (user_id,)
            ).fetchone()
            return bool(row[0]) if row else False

    def set_enabled(self, user_id: str, enabled: bool) -> dict:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("verified user_id is required")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("""
                INSERT INTO memory_consent(user_id, enabled, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled=excluded.enabled, updated_at=excluded.updated_at
            """, (user_id, int(enabled), updated_at))
        return {"enabled": enabled, "updated_at": updated_at}
