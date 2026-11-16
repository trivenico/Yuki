import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from automation.execution_task import DurableSandboxExecutionWorker, SandboxExecutionTask
from automation.sandbox_execution_approval import (
    SQLiteSandboxExecutionApprovalStore, SandboxExecutionAuditWriter,
)
from automation.sandbox_execution_submission import SandboxExecutionSubmissionService
from core.approval_audit import SQLiteApprovalAuditLog
from core.auth import BuiltInAuthService
from core.durable_task_engine import DurableTaskEngine, TaskConflict
from hackback.sandbox import DockerSandbox


class SandboxExecutionSubmissionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(
            str(root / "auth.sqlite3"), password_iterations=100_000,
        )
        self.auth.bootstrap_admin("owner.one", "Correct horse battery staple 9!")
        self.session_a = self.auth.authenticate(
            "owner.one", "Correct horse battery staple 9!",
        )
        self.auth.create_account("owner.two", "Another secure password 4!")
        with sqlite3.connect(self.auth.database_path) as db:
            db.execute("UPDATE auth_users SET role='admin' WHERE username='owner.two'")
        self.session_b = self.auth.authenticate(
            "owner.two", "Another secure password 4!",
        )
        self.store = SQLiteSandboxExecutionApprovalStore(
            str(root / "auth.sqlite3"), auth_service=self.auth,
        )
        self.engine = DurableTaskEngine(str(root / "tasks.sqlite3"))
        self.addCleanup(self.engine.close)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        image = "registry.example/yuki@sha256:" + "a" * 64
        self.task = SandboxExecutionTask(
            task_id="submission-test-1", image=image,
            argv=("python", "--version"), workspace=str(self.workspace),
        )
        audit = SQLiteApprovalAuditLog(str(root / "audit.sqlite3"))
        self.sandbox = DockerSandbox(
            str(self.workspace), allowed_images=(image,), enabled=True,
            approver=lambda _reason: True,
        )
        self.sandbox.run = Mock(return_value={
            "returncode": 0, "stdout": "ok", "stderr": "",
        })
        self.worker = DurableSandboxExecutionWorker.from_trusted_stores(
            self.engine, worker_id="sandbox-worker", sandbox=self.sandbox,
            approval_store=self.store,
            audit_writer=SandboxExecutionAuditWriter(audit),
        )
        self.service = SandboxExecutionSubmissionService(
            auth_service=self.auth, engine=self.engine, worker=self.worker,
            approval_store=self.store,
        )

    def test_goal_and_task_visibility_are_owner_scoped(self):
        goal_a = self.service.create_goal(session_token=self.session_a.token)
        goal_b = self.service.create_goal(session_token=self.session_b.token)
        approval_a = self.service.approve_task(
            self.task, session_token=self.session_a.token,
        )
        queued = self.service.submit(
            goal_a, self.task, approval_a, idempotency_key="owner-a",
            session_token=self.session_a.token,
        )
        self.assertEqual(
            self.service.get_task(queued["id"], session_token=self.session_a.token)["id"],
            queued["id"],
        )
        with self.assertRaises(PermissionError):
            self.service.list_tasks(goal_a, session_token=self.session_b.token)
        with self.assertRaises(PermissionError):
            self.service.get_task(queued["id"], session_token=self.session_b.token)
        with self.assertRaises(PermissionError):
            self.service.submit(
                goal_b, self.task, approval_a, idempotency_key="cross-goal",
                session_token=self.session_b.token,
            )

    def test_cross_user_approval_cannot_be_submitted(self):
        goal_b = self.service.create_goal(session_token=self.session_b.token)
        approval_a = self.service.approve_task(
            self.task, session_token=self.session_a.token,
        )
        with self.assertRaises(PermissionError):
            self.service.submit(
                goal_b, self.task, approval_a, idempotency_key="cross-user",
                session_token=self.session_b.token,
            )

    def test_revoked_submitter_session_is_rejected(self):
        goal = self.service.create_goal(session_token=self.session_a.token)
        approval = self.service.approve_task(
            self.task, session_token=self.session_a.token,
        )
        self.auth.revoke_session(self.session_a.token)
        with self.assertRaises(PermissionError):
            self.service.submit(
                goal, self.task, approval, idempotency_key="revoked-session",
                session_token=self.session_a.token,
            )

    def test_revoked_approval_cannot_be_queued(self):
        goal = self.service.create_goal(session_token=self.session_a.token)
        approval = self.service.approve_task(
            self.task, session_token=self.session_a.token,
        )
        self.store.revoke(approval.approval_id, session_token=self.session_a.token)
        with self.assertRaises(PermissionError):
            self.service.submit(
                goal, self.task, approval, idempotency_key="revoked-approval",
                session_token=self.session_a.token,
            )

    def test_duplicate_submission_remains_idempotent_after_approval_consumption(self):
        goal = self.service.create_goal(session_token=self.session_a.token)
        approval = self.service.approve_task(
            self.task, session_token=self.session_a.token,
        )
        first = self.service.submit(
            goal, self.task, approval, idempotency_key="same-request",
            session_token=self.session_a.token,
        )
        self.assertTrue(self.store.consume(approval, self.task))
        second = self.service.submit(
            goal, self.task, approval, idempotency_key="same-request",
            session_token=self.session_a.token,
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.engine.list_tasks(goal)), 1)

    def test_idempotency_key_cannot_be_rebound_to_a_new_approval(self):
        goal = self.service.create_goal(session_token=self.session_a.token)
        first_approval = self.service.approve_task(self.task, session_token=self.session_a.token)
        self.service.submit(goal, self.task, first_approval, idempotency_key="fixed-key",
                            session_token=self.session_a.token)
        second_approval = self.service.approve_task(self.task, session_token=self.session_a.token)
        with self.assertRaises(TaskConflict):
            self.service.submit(goal, self.task, second_approval, idempotency_key="fixed-key",
                                session_token=self.session_a.token)
    def test_unprivileged_user_cannot_create_approval_or_submit(self):
        self.auth.create_account("ordinary.user", "A different secure password 5!")
        ordinary = self.auth.authenticate(
            "ordinary.user", "A different secure password 5!",
        )
        with self.assertRaises(PermissionError):
            self.service.approve_task(self.task, session_token=ordinary.token)
        with self.assertRaises(PermissionError):
            self.service.create_goal(session_token="invalid-session-token")

if __name__ == "__main__":
    unittest.main()
