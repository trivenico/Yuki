"""Metadata-only SQLite audit log for approval state transitions."""

import hashlib
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


class SQLiteApprovalAuditLog:
    def __init__(self, database_path):
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS approval_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, event_type TEXT NOT NULL, pending_id_hash TEXT NOT NULL, skill_id TEXT NOT NULL, actor_hash TEXT NOT NULL, session_hash TEXT NOT NULL, arguments_hash TEXT NOT NULL)"
            )

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    @staticmethod
    def _hash(value):
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    def record(self, event_type, *, pending_id, skill_id, actor, session_id, arguments):
        if event_type not in {"requested", "approved", "denied", "executed", "failed"}:
            raise ValueError("unsupported audit event")
        args_hash = self._hash(
            json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False)
        )
        with self._connect() as db:
            db.execute(
                "INSERT INTO approval_audit(timestamp,event_type,pending_id_hash,skill_id,actor_hash,session_hash,arguments_hash) VALUES(?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    event_type,
                    self._hash(pending_id),
                    skill_id,
                    self._hash(actor),
                    self._hash(session_id),
                    args_hash,
                ),
            )

    def list_events(self):
        with self._connect() as db:
            rows = db.execute(
                "SELECT timestamp,event_type,pending_id_hash,skill_id,actor_hash,session_hash,arguments_hash FROM approval_audit ORDER BY id DESC"
            ).fetchall()
        keys = (
            "timestamp",
            "event_type",
            "pending_id_hash",
            "skill_id",
            "actor_hash",
            "session_hash",
            "arguments_hash",
        )
        return [dict(zip(keys, row)) for row in rows]
