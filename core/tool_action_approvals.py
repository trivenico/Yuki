"""Durable, single-use approvals bound to one trusted tool action.

Only the authenticated host should call issue() after recording an explicit user
approval. The model must never receive authority to mint or approve these records.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import secrets
import sqlite3
import hashlib

from core.sqlite_file_security import prepare_private_sqlite_path
from core.tool_action_context import ToolActionContext


class ToolActionApprovalError(RuntimeError):
    """Raised when durable approval storage cannot safely complete an operation."""


class SQLiteToolActionApprovalStore:
    """SQLite-backed approvals bound to principal, action, tool and request digest."""

    def __init__(self, database_path: str, *, ttl_seconds: int = 300):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path.")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be between 1 and 3600.")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.ttl_seconds = ttl_seconds
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tool_action_approvals (
                approval_id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_tool_action_approval_expiry
                ON tool_action_approvals(expires_at)""")
            db.execute("""CREATE TABLE IF NOT EXISTS tool_action_approval_audit (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                principal_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                token_fingerprint TEXT NOT NULL,
                outcome TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_tool_action_approval_audit_principal
                ON tool_action_approval_audit(principal_id, event_id)""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout = 5000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _validate_context(context: ToolActionContext, principal_id: str) -> None:
        if not isinstance(context, ToolActionContext):
            raise TypeError("context must be a host-created ToolActionContext.")
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("authenticated principal is required.")
        if context.principal_id != principal_id:
            raise ValueError("approval principal does not match the action context.")


    @staticmethod
    def _fingerprint(approval_id: str) -> str:
        return hashlib.sha256(approval_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _audit(db, *, principal_id: str, action_id: str, tool_name: str,
               token_fingerprint: str, outcome: str, occurred_at: str) -> None:
        db.execute("""INSERT INTO tool_action_approval_audit
            (principal_id, action_id, tool_name, token_fingerprint, outcome, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (principal_id, action_id, tool_name, token_fingerprint, outcome, occurred_at))

    def issue(self, context: ToolActionContext, *, principal_id: str) -> str:
        """Mint a one-time token after trusted UI/server code records user approval."""
        self._validate_context(context, principal_id)
        now = datetime.now(timezone.utc)
        approval_id = secrets.token_urlsafe(32)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM tool_action_approvals WHERE expires_at <= ?",
                           (now.isoformat(),))
                db.execute("""INSERT INTO tool_action_approvals
                    (approval_id, principal_id, action_id, tool_name, request_digest,
                     created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (approval_id, principal_id, context.action_id, context.tool_name,
                     context.request_digest, now.isoformat(),
                     (now + timedelta(seconds=self.ttl_seconds)).isoformat()))
                self._audit(db, principal_id=principal_id, action_id=context.action_id,
                            tool_name=context.tool_name,
                            token_fingerprint=self._fingerprint(approval_id),
                            outcome="issued", occurred_at=now.isoformat())
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return approval_id


    def consume(self, approval_id: str, context: ToolActionContext, *,
                principal_id: str) -> bool:
        """Atomically validate and consume an approval; retries cannot reuse it."""
        self._validate_context(context, principal_id)
        if not isinstance(approval_id, str) or not approval_id or len(approval_id) > 128:
            return False
        now = datetime.now(timezone.utc).isoformat()
        fingerprint = self._fingerprint(approval_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("""SELECT principal_id, action_id, tool_name,
                    request_digest, expires_at FROM tool_action_approvals
                    WHERE approval_id = ?""", (approval_id,)).fetchone()
                outcome = "not_found"
                if row is not None:
                    matches = (
                        row[0] == principal_id and row[1] == context.action_id
                        and row[2] == context.tool_name and row[3] == context.request_digest
                        and row[4] > now
                    )
                    if matches:
                        outcome = "consumed"
                        db.execute("DELETE FROM tool_action_approvals WHERE approval_id = ?",
                                   (approval_id,))
                    elif row[4] <= now:
                        outcome = "expired"
                        db.execute("DELETE FROM tool_action_approvals WHERE approval_id = ?",
                                   (approval_id,))
                    else:
                        outcome = "binding_mismatch"
                else:
                    matches = False
                self._audit(db, principal_id=principal_id, action_id=context.action_id,
                            tool_name=context.tool_name, token_fingerprint=fingerprint,
                            outcome=outcome, occurred_at=now)
                db.execute("COMMIT")
                return bool(row is not None and outcome == "consumed")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def revoke(self, approval_id: str, *, principal_id: str) -> bool:
        """Revoke a pending approval owned by the authenticated principal."""
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("authenticated principal is required.")
        if not isinstance(approval_id, str) or not approval_id or len(approval_id) > 128:
            return False
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("""SELECT action_id, tool_name FROM tool_action_approvals
                    WHERE approval_id = ? AND principal_id = ?""",
                    (approval_id, principal_id)).fetchone()
                if row is None:
                    db.execute("COMMIT")
                    return False
                db.execute("""DELETE FROM tool_action_approvals
                    WHERE approval_id = ? AND principal_id = ?""", (approval_id, principal_id))
                self._audit(db, principal_id=principal_id, action_id=row[0],
                            tool_name=row[1], token_fingerprint=self._fingerprint(approval_id),
                            outcome="revoked", occurred_at=now)
                db.execute("COMMIT")
                return True
            except Exception:
                db.execute("ROLLBACK")
                raise

    def audit_events(self, *, principal_id: str, limit: int = 100) -> tuple[dict, ...]:
        """Return bounded audit metadata for one authenticated principal."""
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("authenticated principal is required.")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000.")
        with self._connect() as db:
            rows = db.execute("""SELECT event_id, action_id, tool_name,
                token_fingerprint, outcome, occurred_at
                FROM tool_action_approval_audit WHERE principal_id = ?
                ORDER BY event_id DESC LIMIT ?""", (principal_id, limit)).fetchall()
        return tuple({
            "event_id": row[0], "action_id": row[1], "tool_name": row[2],
            "token_fingerprint": row[3], "outcome": row[4], "occurred_at": row[5],
        } for row in rows)
