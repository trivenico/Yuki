"""Authenticated, durable approvals for one-shot sandbox execution tasks.

Approval creation resolves a live BuiltInAuthService session; execution rechecks
that same session, account status, permission, approval record and task digest.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import secrets
import sqlite3
from typing import Any

from core.auth import BuiltInAuthService
from core.sqlite_file_security import prepare_private_sqlite_path
from automation.execution_task import ExecutionApproval, SandboxExecutionTask


class SQLiteSandboxExecutionApprovalStore:
    PERMISSION = "workflow:approve"

    def __init__(self, database_path: str, *, auth_service: BuiltInAuthService):
        if not isinstance(auth_service, BuiltInAuthService):
            raise TypeError("auth_service must be a BuiltInAuthService")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.auth_service = auth_service
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS sandbox_execution_approvals (
                approval_id TEXT PRIMARY KEY,
                actor_id TEXT NOT NULL,
                session_hash TEXT NOT NULL,
                task_id TEXT NOT NULL,
                task_fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_sandbox_approval_task ON sandbox_execution_approvals(task_id)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def approve(self, task: SandboxExecutionTask, *, session_token: str) -> ExecutionApproval:
        task.validate()
        if not isinstance(session_token, str):
            raise PermissionError("A live authenticated session is required.")
        user = self.auth_service.resolve_session(session_token)
        if user is None or self.PERMISSION not in user.permissions:
            raise PermissionError("The authenticated principal cannot approve execution.")
        approval_id = secrets.token_urlsafe(24)
        session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
        fingerprint = task.fingerprint()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO sandbox_execution_approvals
                (approval_id,actor_id,session_hash,task_id,task_fingerprint,created_at)
                VALUES (?,?,?,?,?,?)""",
                (approval_id, user.user_id, session_hash, task.task_id, fingerprint,
                 datetime.now(timezone.utc).isoformat()))
        return ExecutionApproval(approval_id, user.user_id, task.task_id, fingerprint, True)

    def verify(self, approval: ExecutionApproval, task: SandboxExecutionTask) -> bool:
        if not isinstance(approval, ExecutionApproval) or not isinstance(task, SandboxExecutionTask):
            return False
        if approval.approved is not True or approval.task_id != task.task_id:
            return False
        if approval.task_fingerprint != task.fingerprint():
            return False
        with self._connect() as db:
            row = db.execute("""SELECT actor_id,session_hash,task_id,task_fingerprint,revoked_at
                FROM sandbox_execution_approvals WHERE approval_id=?""", (approval.approval_id,)).fetchone()
        if row is None or row["revoked_at"] is not None:
            return False
        if (row["actor_id"] != approval.actor_id or row["task_id"] != task.task_id
                or row["task_fingerprint"] != task.fingerprint()):
            return False
        # Resolve by digest without retaining the bearer token in this store.
        return self.auth_service.resolve_session_hash(row["session_hash"], expected_user_id=row["actor_id"]) is True

    def revoke(self, approval_id: str, *, session_token: str) -> bool:
        user = self.auth_service.resolve_session(session_token) if isinstance(session_token, str) else None
        if user is None or "users:manage" not in user.permissions:
            raise PermissionError("An authenticated administrator is required to revoke approvals.")
        with self._connect() as db:
            result = db.execute("""UPDATE sandbox_execution_approvals SET revoked_at=?
                WHERE approval_id=? AND revoked_at IS NULL""",
                (datetime.now(timezone.utc).isoformat(), approval_id))
            return result.rowcount == 1
