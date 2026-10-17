"""SQLite persistence for pending approval snapshots.

Pending actions are short-lived operational state, not an audit log. Store only
validated JSON snapshots; callers must still authenticate actor/session context.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StoredPendingAction:
    pending_id: str
    skill_id: str
    arguments: dict[str, Any]
    action: dict[str, Any]
    expires_at: str


class SQLitePendingApprovalStore:
    """Bounded, restart-persistent pending approvals with atomic one-time claims."""

    def __init__(self, database_path: str, *, ttl_seconds: int = 300, max_pending: int = 128):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be between 1 and 3600.")
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096.")
        self.database_path = str(Path(database_path).expanduser())
        self.ttl_seconds = ttl_seconds
        self.max_pending = max_pending
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS pending_approvals (
                pending_id TEXT PRIMARY KEY,
                skill_id TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                action_json TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_pending_approval_expiry ON pending_approvals(expires_at)")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    @staticmethod
    def _encode(value: dict[str, Any]) -> str:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 16 * 1024:
            raise ValueError("Pending action exceeds 16 KiB.")
        return encoded

    def put(self, skill_id: str, arguments: dict[str, Any], action: dict[str, Any]) -> StoredPendingAction:
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("skill_id is required.")
        args_json, action_json = self._encode(arguments), self._encode(action)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=self.ttl_seconds)).isoformat()
        item = StoredPendingAction(secrets.token_urlsafe(24), skill_id, json.loads(args_json),
                                   json.loads(action_json), expires)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM pending_approvals WHERE expires_at <= ?", (now.isoformat(),))
                count = db.execute("SELECT COUNT(*) FROM pending_approvals").fetchone()[0]
                if count >= self.max_pending:
                    raise RuntimeError("Pending approval capacity reached.")
                db.execute("INSERT INTO pending_approvals VALUES (?, ?, ?, ?, ?)",
                           (item.pending_id, skill_id, args_json, action_json, expires))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return item

    def get(self, pending_id: str) -> StoredPendingAction | None:
        if not isinstance(pending_id, str) or not pending_id:
            return None
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            row = db.execute("SELECT skill_id, arguments_json, action_json, expires_at "
                             "FROM pending_approvals WHERE pending_id = ?", (pending_id,)).fetchone()
            if row is None:
                return None
            if row[3] <= now:
                db.execute("DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,))
                return None
            return StoredPendingAction(pending_id, row[0], json.loads(row[1]), json.loads(row[2]), row[3])

    def consume(self, pending_id: str) -> StoredPendingAction | None:
        if not isinstance(pending_id, str) or not pending_id:
            return None
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT skill_id, arguments_json, action_json, expires_at "
                                 "FROM pending_approvals WHERE pending_id = ?", (pending_id,)).fetchone()
                db.execute("DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,))
                db.execute("DELETE FROM pending_approvals WHERE expires_at <= ?", (now,))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        if row is None or row[3] <= now:
            return None
        return StoredPendingAction(pending_id, row[0], json.loads(row[1]), json.loads(row[2]), row[3])

    def deny(self, pending_id: str) -> bool:
        if not isinstance(pending_id, str) or not pending_id:
            return False
        with self._connect() as db:
            cursor = db.execute("DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,))
            return cursor.rowcount == 1
