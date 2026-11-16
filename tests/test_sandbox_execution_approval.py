import tempfile
import unittest
from pathlib import Path

from automation.execution_task import SandboxExecutionTask
from automation.sandbox_execution_approval import (
    SQLiteSandboxExecutionApprovalStore, SandboxExecutionAuditWriter,
)
from core.approval_audit import SQLiteApprovalAuditLog
from core.auth import BuiltInAuthService


class SandboxExecutionApprovalStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(
            str(root / "auth.sqlite3"), password_iterations=100_000,
        )
        self.admin = self.auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
        self.session = self.auth.authenticate("root.user", "Correct horse battery staple 9!")
        self.store = SQLiteSandboxExecutionApprovalStore(
            str(root / "approvals.sqlite3"), auth_service=self.auth,
        )
        self.task = SandboxExecutionTask(
            task_id="exec-auth-1",
            image="registry.example/yuki@sha256:" + "a" * 64,
            argv=("python", "--version"),
            workspace=self.tmp.name,
        )

    def test_approval_is_persisted_and_verified_after_store_reopen(self):
        approval = self.store.approve(self.task, session_token=self.session.token)
        reopened = SQLiteSandboxExecutionApprovalStore(
            str(Path(self.tmp.name) / "approvals.sqlite3"), auth_service=self.auth,
        )
        self.assertTrue(reopened.verify(approval, self.task))
        self.assertFalse(reopened.verify(approval, SandboxExecutionTask(
            task_id=self.task.task_id, image=self.task.image,
            argv=("python", "-c", "print(1)"), workspace=self.task.workspace,
        )))

    def test_non_approver_cannot_create_approval(self):
        self.auth.create_account("normal.user", "Another secure password 4!")
        session = self.auth.authenticate("normal.user", "Another secure password 4!")
        with self.assertRaises(PermissionError):
            self.store.approve(self.task, session_token=session.token)

    def test_revoked_or_disabled_approver_session_invalidates_approval(self):
        approval = self.store.approve(self.task, session_token=self.session.token)
        self.assertTrue(self.auth.revoke_session(self.session.token))
        self.assertFalse(self.store.verify(approval, self.task))

    def test_administrator_can_revoke_an_approval(self):
        approval = self.store.approve(self.task, session_token=self.session.token)
        self.assertTrue(self.store.revoke(approval.approval_id, session_token=self.session.token))
        self.assertFalse(self.store.verify(approval, self.task))
        self.assertFalse(self.store.revoke(approval.approval_id, session_token=self.session.token))

    def test_audit_writer_persists_hash_chained_metadata_events(self):
        log = SQLiteApprovalAuditLog(str(Path(self.tmp.name) / "audit.sqlite3"))
        writer = SandboxExecutionAuditWriter(log)
        approval = self.store.approve(self.task, session_token=self.session.token)
        event = {
            "event": "execution.started", "task_id": self.task.task_id,
            "approval_id": approval.approval_id, "actor_id": approval.actor_id,
            "task_fingerprint": self.task.fingerprint(),
        }
        writer(event)
        self.assertTrue(log.verify_chain())
        rows = log.list_events()
        self.assertEqual(rows[0]["event_type"], "execution_started")
        self.assertNotIn(self.task.task_id, str(rows[0]))


if __name__ == "__main__":
    unittest.main()
