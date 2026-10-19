"""Consent-based, user-controlled continuity memory for Yuki.

Only call remember(..., confirmed=True) after the user explicitly asks Yuki to
retain the information. Do not store credentials, authentication tokens, or
highly sensitive data here. This store does not replace backups or user exports.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: str, field: str, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{field} exceeds the {limit}-character limit")
    return value


class CompanionMemory:
    """Small local memory store with explicit save consent and deletion/export."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS companion_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_companion_memory_category ON companion_memories(category)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    def remember(self, category: str, content: str, *, confirmed: bool = False) -> int:
        """Persist a memory only when explicit confirmation is supplied."""
        if confirmed is not True:
            raise PermissionError("explicit user confirmation is required to save memory")
        category = _text(category, "category", 80).lower()
        content = _text(content, "content", 2000)
        now = _now()
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO companion_memories(category,content,created_at,updated_at) VALUES(?,?,?,?)",
                (category, content, now, now),
            )
            return int(cursor.lastrowid)

    def list_memories(self, category: Optional[str] = None) -> list[dict]:
        with self._connect() as db:
            if category is None:
                rows = db.execute(
                    "SELECT id,category,content,created_at,updated_at FROM companion_memories ORDER BY id"
                ).fetchall()
            else:
                category = _text(category, "category", 80).lower()
                rows = db.execute(
                    "SELECT id,category,content,created_at,updated_at FROM companion_memories WHERE category=? ORDER BY id",
                    (category,),
                ).fetchall()
        return [dict(row) for row in rows]

    def delete_memory(self, memory_id: int) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM companion_memories WHERE id=?", (int(memory_id),))
            return cursor.rowcount > 0

    def clear(self, *, confirmed: bool = False) -> int:
        """Delete all memories only after a separate explicit confirmation."""
        if confirmed is not True:
            raise PermissionError("explicit confirmation is required to clear all memories")
        with self._connect() as db:
            cursor = db.execute("DELETE FROM companion_memories")
            return cursor.rowcount

    def export_json(self) -> str:
        """Return user-readable JSON for backup or migration."""
        return json.dumps(self.list_memories(), ensure_ascii=False, indent=2)
