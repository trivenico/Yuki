"""SQLite-backed audit log for approval lifecycle events.

Only identifiers and lifecycle metadata are recorded; action arguments and secrets are
intentionally excluded. The log is append-only through this API.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any


class SQLiteApprovalAuditLog:
    """Small durable audit store for approval events."""

    _EVENTS = frozenset({
        "requested", "approved", "denied", "executed", "execution_failed",
    })

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS approval_audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    pending_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_approval_audit_created "
                "ON approval_audit_events(created_at, event_id)"
            )

    @contextmanager
    def _connect(self):
        """Yield a SQLite connection and always close it (important on Windows)."""
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout = 5000")
            yield db
        finally:
            db.close()

    def append(self, *, event_type: str, pending_id: str, actor: str, skill_id: str) -> int:
        if event_type not in self._EVENTS:
            raise ValueError("Unsupported approval audit event type.")
        for name, value, limit in (
            ("pending_id", pending_id, 128),
            ("actor", actor, 256),
            ("skill_id", skill_id, 120),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{name} must be a non-empty bounded string.")
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO approval_audit_events
                   (event_type, pending_id, actor, skill_id, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (event_type, pending_id, actor, skill_id, created_at),
            )
            return int(cursor.lastrowid)

    def list_events(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000.")
        with self._connect() as db:
            rows = db.execute(
                """SELECT event_id, event_type, pending_id, actor, skill_id, created_at
                   FROM approval_audit_events
                   ORDER BY event_id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "event_id": row[0],
                "event_type": row[1],
                "pending_id": row[2],
                "actor": row[3],
                "skill_id": row[4],
                "created_at": row[5],
            }
            for row in rows
        ]
