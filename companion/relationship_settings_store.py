"""Private, owner-scoped persistence for relationship-mode preferences.

The database is not encrypted at rest. Keep it in the owner's private workspace
and protect backups separately. This store persists preferences only; it does
not schedule check-ins, inject prompts, or grant capabilities.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from companion.relationship_mode import (
    AffectionLevel,
    RelationshipMode,
    RelationshipModeSettings,
)


class RelationshipSettingsStore:
    """Persist one owner's validated relationship settings in a private SQLite DB."""

    MAX_PAYLOAD_BYTES = 16_384

    def __init__(self, database_path: str | Path, *, owner_id: str) -> None:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
            raise ValueError("owner_id must be non-empty text of at most 200 characters.")
        raw_path = Path(database_path).expanduser()
        if raw_path.exists() and raw_path.is_symlink():
            raise ValueError("relationship settings database must not be a symlink.")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_path.parent.is_symlink():
            raise ValueError("relationship settings directory must not be a symlink.")
        self.database_path = raw_path
        self.owner_id = owner_id.strip()
        self._initialize()
        if os.name == "posix":
            try:
                os.chmod(self.database_path, 0o600)
            except OSError as exc:
                raise RuntimeError("Could not secure relationship settings permissions.") from exc

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self.database_path.is_symlink():
            raise RuntimeError("relationship settings database must not be a symlink.")
        db = sqlite3.connect(str(self.database_path), timeout=10, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA foreign_keys=ON")
            yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS relationship_settings (
                    owner_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

    @staticmethod
    def _encode(settings: RelationshipModeSettings) -> str:
        if not isinstance(settings, RelationshipModeSettings):
            raise TypeError("settings must be RelationshipModeSettings.")
        payload = {
            "mode": settings.mode.value,
            "affection": settings.affection.value,
            "adult_confirmed": settings.adult_confirmed,
            "memory_enabled": settings.memory_enabled,
            "proactive_checkins_enabled": settings.proactive_checkins_enabled,
            "quiet_hours_start": settings.quiet_hours_start,
            "quiet_hours_end": settings.quiet_hours_end,
            "topics_to_avoid": list(settings.topics_to_avoid),
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > RelationshipSettingsStore.MAX_PAYLOAD_BYTES:
            raise ValueError("relationship settings exceed the storage limit.")
        return encoded

    @staticmethod
    def _decode(payload: str) -> RelationshipModeSettings:
        try:
            data: Any = json.loads(payload)
            expected = {
                "mode", "affection", "adult_confirmed", "memory_enabled",
                "proactive_checkins_enabled", "quiet_hours_start", "quiet_hours_end",
                "topics_to_avoid",
            }
            if not isinstance(data, dict) or set(data) != expected:
                raise ValueError("invalid settings shape")
            if not isinstance(data["topics_to_avoid"], list):
                raise ValueError("invalid topics")
            return RelationshipModeSettings(
                mode=RelationshipMode(data["mode"]),
                affection=AffectionLevel(data["affection"]),
                adult_confirmed=data["adult_confirmed"],
                memory_enabled=data["memory_enabled"],
                proactive_checkins_enabled=data["proactive_checkins_enabled"],
                quiet_hours_start=data["quiet_hours_start"],
                quiet_hours_end=data["quiet_hours_end"],
                topics_to_avoid=tuple(data["topics_to_avoid"]),
            )
        except (TypeError, KeyError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Stored relationship settings are invalid.") from exc

    def load(self) -> RelationshipModeSettings:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM relationship_settings WHERE owner_id=?",
                (self.owner_id,),
            ).fetchone()
        return RelationshipModeSettings() if row is None else self._decode(row["payload"])

    def save(self, settings: RelationshipModeSettings) -> RelationshipModeSettings:
        payload = self._encode(settings)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO relationship_settings(owner_id,payload,updated_at)
                   VALUES(?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(owner_id) DO UPDATE SET
                     payload=excluded.payload, updated_at=CURRENT_TIMESTAMP""",
                (self.owner_id, payload),
            )
            db.commit()
        return settings

    def reset(self) -> None:
        """Delete the saved preferences; the next load returns safe defaults."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM relationship_settings WHERE owner_id=?", (self.owner_id,))
            db.commit()
