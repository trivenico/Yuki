"""SQLite persistence for pending approval snapshots.

Pending actions are short-lived operational state. Creator actor/session bindings are
hashed at rest and checked again at approval time; callers must provide trusted identity.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from core.sqlite_file_security import prepare_private_sqlite_path
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
    status: str = "pending"
    created: bool = True


class SQLitePendingApprovalStore:
    """Bounded, restart-persistent approval records with atomic lifecycle transitions."""

    def __init__(self, database_path: str, *, ttl_seconds: int = 300, max_pending: int = 128):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be between 1 and 3600.")
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096.")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.ttl_seconds = ttl_seconds
        self.max_pending = max_pending
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS pending_approvals (
                pending_id TEXT PRIMARY KEY,
                skill_id TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                action_json TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                actor_hash TEXT,
                session_hash TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(pending_approvals)")}
            if "actor_hash" not in columns:
                db.execute("ALTER TABLE pending_approvals ADD COLUMN actor_hash TEXT")
            if "session_hash" not in columns:
                db.execute("ALTER TABLE pending_approvals ADD COLUMN session_hash TEXT")
            if "status" not in columns:
                db.execute("ALTER TABLE pending_approvals ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
            if "idempotency_hash" not in columns:
                db.execute("ALTER TABLE pending_approvals ADD COLUMN idempotency_hash TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS idx_pending_approval_expiry ON pending_approvals(expires_at)")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_approval_idempotency
                ON pending_approvals(actor_hash, session_hash, idempotency_hash)
                WHERE idempotency_hash IS NOT NULL""")

    @contextmanager
    def _connect(self):
        """Yield a SQLite connection and always close it (important on Windows)."""
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout = 5000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _identity_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _encode(value: dict[str, Any]) -> str:
        if not isinstance(value, dict):
            raise ValueError("Pending action snapshots must be objects.")
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 16 * 1024:
            raise ValueError("Pending action exceeds 16 KiB.")
        return encoded

    def put(self, skill_id: str, arguments: dict[str, Any], action: dict[str, Any], *,
            actor: str, session_id: str, idempotency_key: str | None = None) -> StoredPendingAction:
        if not isinstance(skill_id, str) or not skill_id.strip() or len(skill_id) > 120:
            raise ValueError("skill_id is required and must be bounded.")
        if not isinstance(actor, str) or not actor.strip() or not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("Trusted actor and session identity are required.")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip()
            or len(idempotency_key) > 256
        ):
            raise ValueError("idempotency_key must be non-empty bounded text")
        args_json, action_json = self._encode(arguments), self._encode(action)
        action_hash = hashlib.sha256(action_json.encode("utf-8")).hexdigest()
        idempotency_hash = (
            self._identity_hash(idempotency_key) if idempotency_key is not None else None
        )
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=self.ttl_seconds)).isoformat()
        actor_hash, session_hash = self._identity_hash(actor), self._identity_hash(session_id)
        item = StoredPendingAction(secrets.token_urlsafe(24), skill_id, json.loads(args_json),
                                   json.loads(action_json), expires, actor_hash, session_hash)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if idempotency_hash is not None:
                    existing = db.execute("""SELECT pending_id, skill_id, arguments_json,
                        action_json, expires_at, actor_hash, session_hash, status
                        FROM pending_approvals
                        WHERE actor_hash=? AND session_hash=? AND idempotency_hash=?""",
                        (actor_hash, session_hash, idempotency_hash)).fetchone()
                    if existing is not None:
                        existing_hash = hashlib.sha256(existing[3].encode("utf-8")).hexdigest()
                        if existing_hash != action_hash:
                            raise ValueError("idempotency key was already used for a different action")
                        db.execute("COMMIT")
                        return StoredPendingAction(
                            existing[0], existing[1], json.loads(existing[2]), json.loads(existing[3]),
                            existing[4], existing[5], existing[6], existing[7], False,
                        )
                db.execute("""DELETE FROM pending_approvals
                    WHERE expires_at <= ? AND status IN ('pending', 'completed', 'denied')""",
                    (now.isoformat(),))
                count = db.execute("SELECT COUNT(*) FROM pending_approvals").fetchone()[0]
                if count >= self.max_pending:
                    raise RuntimeError("Pending approval capacity reached.")
                db.execute("""INSERT INTO pending_approvals
                    (pending_id, skill_id, arguments_json, action_json, expires_at, actor_hash, session_hash, status, idempotency_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                    (item.pending_id, skill_id, args_json, action_json, expires, actor_hash, session_hash, idempotency_hash))
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
            row = db.execute("""SELECT skill_id, arguments_json, action_json, expires_at, actor_hash, session_hash, status
                FROM pending_approvals WHERE pending_id = ?""", (pending_id,)).fetchone()
            if row is None:
                return None
            # outcome_unknown is a durable reconciliation record, not a TTL-bound
            # pending request. Keep it until an explicit reconciliation workflow
            # resolves it; deleting it could erase evidence of an external side effect.
            if row[3] <= now and row[6] in {"pending", "completed", "denied"}:
                db.execute("DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,))
                return None
            # Old rows from before identity binding are rejected, not silently upgraded.
            if not row[4] or not row[5]:
                db.execute("DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,))
                return None
            return StoredPendingAction(pending_id, row[0], json.loads(row[1]), json.loads(row[2]),
                                       row[3], row[4], row[5], row[6])

    def get_for_identity(self, pending_id: str, *, actor: str, session_id: str) -> StoredPendingAction | None:
        """Read an approval only when both authenticated identity bindings match."""
        if (not isinstance(pending_id, str) or not pending_id or
                not isinstance(actor, str) or not actor.strip() or
                not isinstance(session_id, str) or not session_id.strip()):
            return None
        now = datetime.now(timezone.utc).isoformat()
        actor_hash, session_hash = self._identity_hash(actor), self._identity_hash(session_id)
        with self._connect() as db:
            row = db.execute("""SELECT skill_id, arguments_json, action_json, expires_at,
                actor_hash, session_hash, status FROM pending_approvals
                WHERE pending_id=? AND actor_hash=? AND session_hash=?""",
                (pending_id, actor_hash, session_hash)).fetchone()
            if row is None:
                return None
            if row[3] <= now and row[6] in {"pending", "completed", "denied"}:
                db.execute("DELETE FROM pending_approvals WHERE pending_id=?", (pending_id,))
                return None
            return StoredPendingAction(pending_id, row[0], json.loads(row[1]), json.loads(row[2]),
                row[3], row[4], row[5], row[6])

    def transition(self, pending_id: str, *, expected_status: str, new_status: str,
                   actor: str, session_id: str) -> StoredPendingAction | None:
        """Atomically transition an owner/session-bound approval without deleting it."""
        allowed = {"pending", "claimed", "approved", "executing", "completed",
                   "denied", "execution_unavailable", "outcome_unknown"}
        transitions = {
            "pending": {"claimed", "denied"},
            "claimed": {"approved", "denied"},
            "approved": {"executing", "execution_unavailable"},
            "executing": {"completed", "outcome_unknown"},
            "execution_unavailable": {"approved", "denied"},
        }
        if (not isinstance(pending_id, str) or not pending_id or
                not isinstance(expected_status, str) or not isinstance(new_status, str) or
                expected_status not in allowed or new_status not in allowed or
                new_status not in transitions.get(expected_status, set()) or
                not isinstance(actor, str) or not actor.strip() or
                not isinstance(session_id, str) or not session_id.strip()):
            return None
        now = datetime.now(timezone.utc).isoformat()
        actor_hash, session_hash = self._identity_hash(actor), self._identity_hash(session_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if expected_status == "pending":
                    cursor = db.execute("""UPDATE pending_approvals SET status=?
                        WHERE pending_id=? AND status=? AND expires_at>? AND actor_hash=? AND session_hash=?""",
                        (new_status, pending_id, expected_status, now, actor_hash, session_hash))
                else:
                    # Once claimed, retain the durable action through approval,
                    # execution, and reconciliation even after its pending TTL.
                    cursor = db.execute("""UPDATE pending_approvals SET status=?
                        WHERE pending_id=? AND status=? AND actor_hash=? AND session_hash=?""",
                        (new_status, pending_id, expected_status, actor_hash, session_hash))
                if cursor.rowcount != 1:
                    db.execute("COMMIT")
                    return None
                row = db.execute("""SELECT skill_id, arguments_json, action_json, expires_at,
                    actor_hash, session_hash, status FROM pending_approvals WHERE pending_id=?""",
                    (pending_id,)).fetchone()
                db.execute("COMMIT")
                return StoredPendingAction(pending_id, row[0], json.loads(row[1]),
                    json.loads(row[2]), row[3], row[4], row[5], row[6])
            except Exception:
                db.execute("ROLLBACK")
                raise

    def consume(self, pending_id: str, *, actor: str, session_id: str) -> StoredPendingAction | None:
        """Legacy destructive claim; new execution flows must use transition()."""
        if not isinstance(pending_id, str) or not pending_id:
            return None
        if not isinstance(actor, str) or not actor.strip() or not isinstance(session_id, str) or not session_id.strip():
            return None
        now = datetime.now(timezone.utc).isoformat()
        actor_hash, session_hash = self._identity_hash(actor), self._identity_hash(session_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("""SELECT skill_id, arguments_json, action_json, expires_at, actor_hash, session_hash, status
                    FROM pending_approvals WHERE pending_id = ?""", (pending_id,)).fetchone()
                if row is None or row[3] <= now or row[4] != actor_hash or row[5] != session_hash:
                    if row is not None and row[3] <= now:
                        db.execute("DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,))
                    db.execute("COMMIT")
                    return None
                db.execute("DELETE FROM pending_approvals WHERE pending_id = ?", (pending_id,))
                db.execute("COMMIT")
                return StoredPendingAction(pending_id, row[0], json.loads(row[1]), json.loads(row[2]),
                                           row[3], row[4], row[5], row[6])
            except Exception:
                db.execute("ROLLBACK")
                raise

    def deny(self, pending_id: str, *, actor: str, session_id: str) -> bool:
        """Owner-bound denial that retains the audit-failure tombstone."""
        return self.transition(
            pending_id, expected_status="pending", new_status="denied",
            actor=actor, session_id=session_id,
        ) is not None
