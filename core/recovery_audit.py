"""Minimal SQLite audit trail for recovery outcomes.

Only fixed-schema metadata is persisted. User-provided reasons, callback output,
exception messages, and credentials are intentionally excluded.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import RLock


class RecoveryAuditLog:
    MAX_ACTION = 80
    MAX_STATUS = 40
    _ALLOWED_STATUSES = frozenset({
        "completed", "failed", "denied", "approval_required",
        "approval_error", "blocked", "invalid", "proposed",
    })

    def __init__(self, path: str | Path, *, max_events: int = 5000) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int) or not 100 <= max_events <= 100000:
            raise ValueError("max_events must be between 100 and 100000")
        self.path = Path(path)
        self.max_events = max_events
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS recovery_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL, "
                "action TEXT NOT NULL, status TEXT NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS recovery_events_time_idx ON recovery_events(id)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=2.0)
        db.execute("PRAGMA busy_timeout = 2000")
        return db

    def record(self, action: str, status: str) -> None:
        if not isinstance(action, str) or not action.strip() or len(action) > self.MAX_ACTION:
            action = "invalid_action"
        if not isinstance(status, str) or status not in self._ALLOWED_STATUSES:
            status = "invalid"
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO recovery_events (occurred_at, action, status) VALUES (?, ?, ?)",
                (timestamp, action, status),
            )
            db.execute(
                "DELETE FROM recovery_events WHERE id NOT IN "
                "(SELECT id FROM recovery_events ORDER BY id DESC LIMIT ?)",
                (self.max_events,),
            )

    def recent(self, limit: int = 50) -> list[dict[str, str]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT occurred_at, action, status FROM recovery_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"occurred_at": row[0], "action": row[1], "status": row[2]}
            for row in rows
        ]
