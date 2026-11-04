"""Owner-scoped structured preferences for Yuki reasoning.

This store is deliberately limited to non-sensitive interaction preferences.
It does not hold credentials, health, financial, biometric, or other sensitive
personal data. Values are typed, versioned, authorized, and never instructions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock


_FIELDS = {
    "response_style": {"type": "enum", "values": {"concise", "balanced", "detailed"}},
    "response_tone": {"type": "enum", "values": {"neutral", "friendly", "formal", "technical"}},
    "preferred_language": {"type": "text", "max": 32},
    "preferred_code_language": {"type": "text", "max": 32},
    "measurement_system": {"type": "enum", "values": {"metric", "imperial"}},
    "date_format": {"type": "enum", "values": {"iso", "day_month_year", "month_day_year"}},
    "code_comments": {"type": "enum", "values": {"minimal", "moderate", "verbose"}},
    "explanation_level": {"type": "enum", "values": {"beginner", "intermediate", "expert"}},
}
_MAX_SOURCE = 240
_KEY = re.compile(r"^[a-z_]{2,48}$")


class OwnerProfileStore:
    """Small typed preference store; authorization is checked on every call."""

    def __init__(self, database_path: str, *, authorize):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path_required")
        if not callable(authorize):
            raise TypeError("trusted_authorize_callback_required")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.authorize = authorize
        self._lock = RLock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS owner_profile_preferences (
                actor_id TEXT NOT NULL,
                preference_key TEXT NOT NULL,
                preference_value TEXT NOT NULL,
                source TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version > 0),
                updated_at TEXT NOT NULL,
                PRIMARY KEY(actor_id, preference_key)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS owner_profile_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id TEXT NOT NULL,
                preference_key TEXT NOT NULL,
                previous_value TEXT,
                new_value TEXT,
                source TEXT NOT NULL,
                version INTEGER NOT NULL,
                occurred_at TEXT NOT NULL
            )""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return _Connection(db)

    @staticmethod
    def _actor(actor_id):
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200:
            raise ValueError("invalid_actor_id")
        return actor_id.strip()

    def _allowed(self, actor_id, action):
        try:
            return self.authorize(actor_id, action, "owner_profile") is True
        except Exception:
            return False

    @staticmethod
    def _validate_value(key, value):
        spec = _FIELDS.get(key)
        if spec is None:
            raise ValueError("unsupported_profile_preference")
        if not isinstance(value, str) or not value.strip() or "\\x00" in value:
            raise ValueError("invalid_profile_preference_value")
        value = value.strip()
        if len(value) > spec.get("max", 48):
            raise ValueError("profile_preference_value_too_long")
        if spec["type"] == "enum" and value not in spec["values"]:
            raise ValueError("invalid_profile_preference_choice")
        return value

    def set(self, *, actor_id, key, value, source, expected_version=None):
        actor_id = self._actor(actor_id)
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError("invalid_profile_preference_key")
        value = self._validate_value(key, value)
        if not isinstance(source, str) or not source.strip() or len(source) > _MAX_SOURCE:
            raise ValueError("profile_source_required")
        if expected_version is not None and (
            type(expected_version) is not int or expected_version < 0
        ):
            raise ValueError("invalid_expected_version")
        if not self._allowed(actor_id, "owner_profile:write"):
            raise PermissionError("owner profile write is not authorized")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM owner_profile_preferences WHERE actor_id=? AND preference_key=?",
                (actor_id, key),
            ).fetchone()
            current_version = row["version"] if row else 0
            if expected_version is not None and expected_version != current_version:
                raise ValueError("profile_version_conflict")
            if row and row["preference_value"] == value:
                return self._record(row)
            version = current_version + 1
            db.execute(
                """INSERT INTO owner_profile_preferences
                   (actor_id,preference_key,preference_value,source,version,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(actor_id,preference_key) DO UPDATE SET
                   preference_value=excluded.preference_value,source=excluded.source,
                   version=excluded.version,updated_at=excluded.updated_at""",
                (actor_id, key, value, source.strip(), version, now),
            )
            db.execute(
                """INSERT INTO owner_profile_events
                   (actor_id,preference_key,previous_value,new_value,source,version,occurred_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (actor_id, key, row["preference_value"] if row else None,
                 value, source.strip(), version, now),
            )
            updated = db.execute(
                "SELECT * FROM owner_profile_preferences WHERE actor_id=? AND preference_key=?",
                (actor_id, key),
            ).fetchone()
        return self._record(updated)

    def get(self, *, actor_id):
        actor_id = self._actor(actor_id)
        if not self._allowed(actor_id, "owner_profile:read"):
            raise PermissionError("owner profile read is not authorized")
        with self._connect() as db:
            rows = db.execute(
                """SELECT preference_key,preference_value,source,version,updated_at
                   FROM owner_profile_preferences WHERE actor_id=? ORDER BY preference_key""",
                (actor_id,),
            ).fetchall()
        return {row["preference_key"]: self._record(row) for row in rows}

    def delete(self, *, actor_id, key, expected_version=None):
        actor_id = self._actor(actor_id)
        if not isinstance(key, str) or key not in _FIELDS:
            raise ValueError("unsupported_profile_preference")
        if expected_version is not None and (
            type(expected_version) is not int or expected_version < 1
        ):
            raise ValueError("invalid_expected_version")
        if not self._allowed(actor_id, "owner_profile:write"):
            raise PermissionError("owner profile write is not authorized")
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM owner_profile_preferences WHERE actor_id=? AND preference_key=?",
                (actor_id, key),
            ).fetchone()
            if row is None:
                return False
            if expected_version is not None and row["version"] != expected_version:
                raise ValueError("profile_version_conflict")
            db.execute(
                "DELETE FROM owner_profile_preferences WHERE actor_id=? AND preference_key=?",
                (actor_id, key),
            )
            db.execute(
                """INSERT INTO owner_profile_events
                   (actor_id,preference_key,previous_value,new_value,source,version,occurred_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (actor_id, key, row["preference_value"], None,
                 "owner_deleted", row["version"] + 1, datetime.now(timezone.utc).isoformat()),
            )
            return True

    @staticmethod
    def _record(row):
        return {
            "key": row["preference_key"],
            "value": row["preference_value"],
            "source": row["source"],
            "version": row["version"],
            "updated_at": row["updated_at"],
            "trust": "owner_preference_not_instruction",
        }


class _Connection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            self.db.commit() if exc_type is None else self.db.rollback()
        finally:
            self.db.close()
        return False
