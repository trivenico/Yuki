"""Small transactional JSON-value store backed by SQLite.

This module is a persistence foundation. Callers must still migrate to it
explicitly; existing JSON files are not changed automatically.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


class SQLiteJsonStore:
    """Store JSON-serializable values with serialized read-modify-write updates."""

    def __init__(self, path, *, timeout_seconds: float = 5.0):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.path = Path(path)
        self.timeout_seconds = float(timeout_seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(
            str(self.path), timeout=self.timeout_seconds, isolation_level=None
        )) as connection:
            connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS yuki_json_state (
                    state_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _connect(self):
        connection = sqlite3.connect(
            str(self.path),
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _encode(value) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    @staticmethod
    def _decode(value_json: str):
        return json.loads(value_json)

    def read_json(self, key: str, default=None):
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value_json FROM yuki_json_state WHERE state_key = ?", (key,)
            ).fetchone()
        return self._decode(row[0]) if row else default

    def write_json(self, key: str, value):
        """Replace one value atomically; raise on serialization or storage failure."""
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        encoded = self._encode(value)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO yuki_json_state(state_key, value_json, revision, updated_at)
                    VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(state_key) DO UPDATE SET
                        value_json = excluded.value_json,
                        revision = yuki_json_state.revision + 1,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, encoded),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def update_json(self, key: str, updater: Callable[[object], object], *, default=None):
        """Atomically read, update, and persist one key under a write transaction.

        The updater runs while the SQLite write lock is held. Keep it deterministic,
        fast, and free of network calls or other side effects.
        """
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        if not callable(updater):
            raise TypeError("updater must be callable")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT value_json FROM yuki_json_state WHERE state_key = ?", (key,)
                ).fetchone()
                current = self._decode(row[0]) if row else default
                updated = updater(current)
                encoded = self._encode(updated)
                connection.execute(
                    """
                    INSERT INTO yuki_json_state(state_key, value_json, revision, updated_at)
                    VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(state_key) DO UPDATE SET
                        value_json = excluded.value_json,
                        revision = yuki_json_state.revision + 1,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, encoded),
                )
                connection.commit()
                return updated
            except Exception:
                connection.rollback()
                raise

    def revision(self, key: str) -> Optional[int]:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT revision FROM yuki_json_state WHERE state_key = ?", (key,)
            ).fetchone()
        return int(row[0]) if row else None
