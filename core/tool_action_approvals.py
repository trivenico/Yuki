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
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("""SELECT principal_id, action_id, tool_name,
                    request_digest, expires_at FROM tool_action_approvals
                    WHERE approval_id = ?""", (approval_id,)).fetchone()
                if row is None:
                    db.execute("COMMIT")
                    return False
                matches = (
                    row[0] == principal_id
                    and row[1] == context.action_id
                    and row[2] == context.tool_name
                    and row[3] == context.request_digest
                    and row[4] > now
                )
                if matches or row[4] <= now:
                    db.execute("DELETE FROM tool_action_approvals WHERE approval_id = ?",
                               (approval_id,))
                db.execute("COMMIT")
                return matches
            except Exception:
                db.execute("ROLLBACK")
                raise
