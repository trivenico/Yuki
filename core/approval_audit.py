"""Append-only structured audit events; action arguments are never stored."""
import json
import sqlite3
import time
import uuid
from pathlib import Path

class SQLiteApprovalAuditLog:
    def __init__(self, database_path):
        self.path = str(Path(database_path).expanduser())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS approval_audit (id TEXT PRIMARY KEY, ts REAL NOT NULL, event_type TEXT NOT NULL, pending_id TEXT, skill_id TEXT, actor_hash TEXT, session_hash TEXT, outcome TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS approval_audit_ts ON approval_audit(ts)")
    def _connect(self):
        db=sqlite3.connect(self.path, timeout=5.0); db.row_factory=sqlite3.Row; db.execute("PRAGMA busy_timeout=5000"); return db
    def record(self, event_type, *, pending_id=None, skill_id=None, actor_hash=None, session_hash=None, outcome="ok"):
        if event_type not in {"requested", "approved", "denied", "executed", "expired", "failed"}: raise ValueError("invalid audit event")
        with self._connect() as db:
            db.execute("INSERT INTO approval_audit VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex, time.time(), event_type, pending_id, skill_id, actor_hash, session_hash, str(outcome)[:80]))
    def list_events(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000: raise ValueError("limit must be 1..1000")
        with self._connect() as db:
            rows=db.execute("SELECT ts,event_type,pending_id,skill_id,actor_hash,session_hash,outcome FROM approval_audit ORDER BY ts DESC LIMIT ?",(limit,)).fetchall()
            return [dict(row) for row in rows]
