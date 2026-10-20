"""SQLite persistence for pending approval snapshots.

Pending actions are short-lived operational state. Creator actor/session bindings are
hashed at rest and checked again at approval time; callers must provide trusted identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any


@dataclass(frozen=True)
class StoredPendingAction:
    pending_id: str
    skill_id: str
    arguments: dict[str, Any]
    action: dict[str, Any]
    expires_at: str
    actor_hash: str
    session_hash: str


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
                expires_at TEXT NOT NULL,
                actor_hash TEXT,
                session_hash TEXT
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(pending_approvals)")}
            if "actor_hash" not in columns:
                db.execute("ALTER TABLE pending_approvals ADD COLUMN actor_hash TEXT")
            if "session_hash" not in columns:
                db.execute("ALTER TABLE pending_approvals ADD COLUMN session_hash TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_pending_approval_expiry ON pending_approvals(expires_at)"
            )

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    @staticmethod
    def _identity_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _encode(value: dict[str, Any]) -> str:
        if not isinstance(value, dict):
            raise ValueError("Pending action snapshots must be objects.")
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        if len(encoded.encode("utf-8")) > 16 * 1024:
            raise ValueError("Pending action exceeds 16 KiB.")
        return encoded

    def put(
        self,
        skill_id: str,
        arguments: dict[str, Any],
        action: dict[str, Any],
        *,
        actor: str,
        session_id: str,
    ) -> StoredPendingAction:
        if not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 120:
            raise ValueError("skill_id is required and must be bounded.")
        if (
            not isinstance(actor, str)
            or not actor.strip()
            or not isinstance(session_id, str)
            or not session_id.strip()
        ):
            raise ValueError("Trusted actor and session identity are required.")
        args_json, action_json = self._encode(arguments), self._encode(action)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=self.ttl_seconds)).isoformat()
        actor_hash, session_hash = self._identity_hash(actor), self._identity_hash(session_id)
        item = StoredPendingAction(
            secrets.token_urlsafe(24),
            skill_id,
            json.loads(args_json),
            json.loads(action_json),
            expires,
            actor_hash,
            session_hash,
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "DELETE FROM pending_approvals WHERE expires_at <= ?", (now.isoformat(),)
                )
                count = db.execute("SELECT COUNT(*) FROM pending_approvals").fetchone()[0]
                if count >= self.max_pending:
                    raise RuntimeError("Pending approval capacity reached.")
                db.execute(
                    """INSERT INTO pending_approvals
                    (pending_id, skill_id, arguments_json, action_json, expires_at, actor_hash, session_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.pending_id,
                        skill_id,
                        args_json,
                        action_json,
                        expires,
                        actor_hash,
                        session_hash,
                    ),
                )
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
            row = db.execute(
                """SELECT skill_id, arguments_json, action_json, expires_at, actor_hash, session_hash
                FROM pending_approvals WHERE pending_id = ?""",
                (pending_id,),
            ).fetchone()
            if row is None:
                return None
            if row[3] <= now:
                db.execute("DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,))
                return None
            # Old rows from before identity binding are rejected, not silently upgraded.
            if not row[4] or not row[5]:
                db.execute("DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,))
                return None
            return StoredPendingAction(
                pending_id, row[0], json.loads(row[1]), json.loads(row[2]), row[3], row[4], row[5]
            )

    def consume(
        self, pending_id: str, *, actor: str, session_id: str
    ) -> StoredPendingAction | None:
        if not isinstance(pending_id, str) or not pending_id:
            return None
        if (
            not isinstance(actor, str)
            or not actor.strip()
            or not isinstance(session_id, str)
            or not session_id.strip()
        ):
            return None
        now = datetime.now(timezone.utc).isoformat()
        actor_hash, session_hash = self._identity_hash(actor), self._identity_hash(session_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    """SELECT skill_id, arguments_json, action_json, expires_at, actor_hash, session_hash
                    FROM pending_approvals WHERE pending_id = ?""",
                    (pending_id,),
                ).fetchone()
                if row is None or row[3] <= now or row[4] != actor_hash or row[5] != session_hash:
                    if row is not None and row[3] <= now:
                        db.execute(
                            "DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,)
                        )
                    db.execute("COMMIT")
                    return None
                db.execute("DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,))
                db.execute("COMMIT")
                return StoredPendingAction(
                    pending_id,
                    row[0],
                    json.loads(row[1]),
                    json.loads(row[2]),
                    row[3],
                    row[4],
                    row[5],
                )
            except Exception:
                db.execute("ROLLBACK")
                raise

    def deny(self, pending_id: str) -> bool:
        if not isinstance(pending_id, str) or not pending_id:
            return False
        with self._connect() as db:
            cursor = db.execute("DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,))
            return cursor.rowcount == 1
