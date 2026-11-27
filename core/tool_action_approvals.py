"""Durable one-shot approvals bound to an exact guarded-tool action.

The caller must obtain owner/session identity from a trusted authentication
boundary. This store does not authenticate that identity or sandbox handlers.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3

from core.sqlite_file_security import prepare_private_sqlite_path
from core.tool_action import ToolActionEnvelope

_HEX_256 = re.compile(r"^[0-9a-f]{64}$")


def action_approval_digest(action: ToolActionEnvelope, target_scope: str) -> str:
    if not isinstance(action, ToolActionEnvelope):
        raise TypeError("action must be a ToolActionEnvelope")
    if not isinstance(target_scope, str) or not target_scope.strip() or len(target_scope) > 512:
        raise ValueError("target_scope must be non-empty bounded text")
    if not isinstance(action.tool_name, str) or not action.tool_name or len(action.tool_name) > 128:
        raise ValueError("invalid tool identity")
    if not _HEX_256.fullmatch(action.argument_sha256 or ""):
        raise ValueError("invalid argument digest")
    if not _HEX_256.fullmatch(action.implementation_sha256 or ""):
        raise ValueError("invalid implementation digest")
    if not action.owner_id or len(action.owner_id) > 256 or not action.session_id or len(action.session_id) > 256:
        raise ValueError("trusted owner and session identity are required")
    if type(action.argument_bytes) is not int or action.argument_bytes < 0:
        raise ValueError("invalid argument length")
    canonical = json.dumps(
        {
            "schema": "yuki.tool-approval.v1",
            "tool": action.tool_name,
            "arguments_sha256": action.argument_sha256,
            "arguments_bytes": action.argument_bytes,
            "implementation_sha256": action.implementation_sha256,
            "target_scope": target_scope,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


class SQLiteToolApprovalStore:
    """Short-lived approval grants with atomic, identity-bound one-time consume."""

    def __init__(self, database_path: str, *, ttl_seconds: int = 300,
                 max_pending: int = 128, maintenance_fence=None):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be between 1 and 3600")
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096")
        if maintenance_fence is not None and not callable(getattr(maintenance_fence, "lease", None)):
            raise TypeError("maintenance_fence must provide lease()")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.ttl_seconds = ttl_seconds
        self.max_pending = max_pending
        self.maintenance_fence = maintenance_fence
        with self._lease(), self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tool_action_approvals (
                approval_id TEXT PRIMARY KEY,
                action_digest TEXT NOT NULL,
                actor_hash TEXT NOT NULL,
                session_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tool_action_approval_expiry ON tool_action_approvals(expires_at)")

    @contextmanager
    def _lease(self):
        if self.maintenance_fence is None:
            yield
        else:
            with self.maintenance_fence.lease():
                yield

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout = 5000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _identity_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def issue(self, action: ToolActionEnvelope, *, target_scope: str) -> str:
        digest = action_approval_digest(action, target_scope)
        actor_hash = self._identity_hash(action.owner_id)
        session_hash = self._identity_hash(action.session_id)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=self.ttl_seconds)).isoformat()
        approval_id = secrets.token_urlsafe(32)
        with self._lease(), self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM tool_action_approvals WHERE expires_at <= ?", (now.isoformat(),))
                count = db.execute("SELECT COUNT(*) FROM tool_action_approvals").fetchone()[0]
                if count >= self.max_pending:
                    raise RuntimeError("Tool approval capacity reached")
                db.execute(
                    "INSERT INTO tool_action_approvals VALUES (?, ?, ?, ?, ?)",
                    (approval_id, digest, actor_hash, session_hash, expires),
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return approval_id

    def consume(self, approval_id: str, action: ToolActionEnvelope, *,
                target_scope: str) -> bool:
        if not isinstance(approval_id, str) or not approval_id or len(approval_id) > 128:
            return False
        try:
            digest = action_approval_digest(action, target_scope)
        except (TypeError, ValueError, AttributeError):
            return False
        actor_hash = self._identity_hash(action.owner_id)
        session_hash = self._identity_hash(action.session_id)
        now = datetime.now(timezone.utc).isoformat()
        with self._lease(), self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT action_digest, actor_hash, session_hash, expires_at "
                    "FROM tool_action_approvals WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
                valid = bool(
                    row is not None
                    and row[3] > now
                    and secrets.compare_digest(row[0], digest)
                    and secrets.compare_digest(row[1], actor_hash)
                    and secrets.compare_digest(row[2], session_hash)
                )
                if valid or (row is not None and row[3] <= now):
                    db.execute("DELETE FROM tool_action_approvals WHERE approval_id = ?", (approval_id,))
                db.execute("COMMIT")
                return valid
            except Exception:
                db.execute("ROLLBACK")
                raise
