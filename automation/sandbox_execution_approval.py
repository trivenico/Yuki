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
                status TEXT NOT NULL DEFAULT 'approved' CHECK(status IN ('approved','consumed','revoked')),
                consumed_at TEXT,
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
            row = db.execute("""SELECT actor_id,session_hash,task_id,task_fingerprint,status
                FROM sandbox_execution_approvals WHERE approval_id=?""", (approval.approval_id,)).fetchone()
        if row is None or row["status"] != "approved":
            return False
        if (row["actor_id"] != approval.actor_id or row["task_id"] != task.task_id
                or row["task_fingerprint"] != task.fingerprint()):
            return False
        # Resolve by digest without retaining the bearer token in this store.
        return self.auth_service.resolve_session_hash(row["session_hash"], expected_user_id=row["actor_id"]) is True


    def consume(self, approval: ExecutionApproval, task: SandboxExecutionTask) -> bool:
        """Atomically consume a valid approval exactly once before external dispatch."""
        if not self.verify(approval, task):
            return False
        consumed_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("""UPDATE sandbox_execution_approvals
                SET status='consumed', consumed_at=?
                WHERE approval_id=? AND actor_id=? AND task_id=?
                  AND task_fingerprint=? AND status='approved'""",
                (consumed_at, approval.approval_id, approval.actor_id,
                 task.task_id, task.fingerprint()))
            return result.rowcount == 1

    def revoke(self, approval_id: str, *, session_token: str) -> bool:
        user = self.auth_service.resolve_session(session_token) if isinstance(session_token, str) else None
        if user is None or "users:manage" not in user.permissions:
            raise PermissionError("An authenticated administrator is required to revoke approvals.")
        with self._connect() as db:
            result = db.execute("""UPDATE sandbox_execution_approvals SET status='revoked', revoked_at=?
                WHERE approval_id=? AND status='approved'""",
                (datetime.now(timezone.utc).isoformat(), approval_id))
            return result.rowcount == 1

class SandboxExecutionAuditWriter:
    """Durable metadata-only audit adapter using Yuki's hash-chained audit log."""

    _EVENTS = {
        "execution.started": "execution_started",
        "execution.completed": "executed",
        "execution.failed": "execution_failed",
    }

    def __init__(self, audit_log):
        from core.approval_audit import SQLiteApprovalAuditLog
        if not isinstance(audit_log, SQLiteApprovalAuditLog):
            raise TypeError("audit_log must be a SQLiteApprovalAuditLog")
        self.audit_log = audit_log

    def __call__(self, event: Any) -> int:
        if not isinstance(event, dict):
            raise ValueError("audit event must be an object")
        event_type = self._EVENTS.get(event.get("event"))
        task_id, actor_id, fingerprint = (
            event.get("task_id"), event.get("actor_id"),
            event.get("task_fingerprint"),
        )
        if event_type is None:
            raise ValueError("unsupported sandbox execution audit event")
        if not all(isinstance(value, str) and value for value in (task_id, actor_id, fingerprint)):
            raise ValueError("sandbox audit identity is incomplete")
        if len(task_id) > 128 or len(actor_id) > 200 or len(fingerprint) != 64:
            raise ValueError("sandbox audit identity is invalid")
        return self.audit_log.append(
            event_type=event_type, pending_id=task_id, actor=actor_id,
            skill_id="sandbox:" + fingerprint,
        )
