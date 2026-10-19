"""Append-only SQLite security audit log; never stores raw action arguments."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

class SQLiteApprovalAuditLog:
    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path is required.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS approval_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
                pending_id TEXT NOT NULL, actor_hash TEXT NOT NULL, skill_id TEXT NOT NULL,
                created_at TEXT NOT NULL, details_json TEXT NOT NULL)""")
        try:
            os.chmod(self.database_path, 0o600)
        except OSError:
            pass

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def append(self, event_type: str, *, pending_id: str, actor_hash: str, skill_id: str, details: dict[str, Any] | None = None):
        if event_type not in {"requested", "approved", "denied", "expired", "executed", "failed"}:
            raise ValueError("Unsupported approval audit event.")
        safe_details = details or {}
        encoded = json.dumps(safe_details, allow_nan=False, sort_keys=True)
        if len(encoded) > 2048:
            raise ValueError("Audit details too large.")
        with self._connect() as db:
            db.execute("INSERT INTO approval_events(event_type,pending_id,actor_hash,skill_id,created_at,details_json) VALUES(?,?,?,?,?,?)",
                       (event_type, pending_id[:128], actor_hash[:128], skill_id[:120],
                        datetime.now(timezone.utc).isoformat(), encoded))

    def list_events(self, limit: int = 500) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("limit must be from 1 to 5000.")
        with self._connect() as db:
            rows = db.execute("SELECT event_type,pending_id,actor_hash,skill_id,created_at,details_json FROM approval_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"event_type": r[0], "pending_id": r[1], "actor_hash": r[2], "skill_id": r[3],
                 "created_at": r[4], "details": json.loads(r[5])} for r in rows]
