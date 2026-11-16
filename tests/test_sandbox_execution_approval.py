import tempfile
import unittest
from pathlib import Path

from automation.execution_task import DurableSandboxExecutionWorker, SandboxExecutionTask, execute_approved_task, ExecutionOutcomeUnknown
from core.durable_task_engine import DurableTaskEngine
from hackback.sandbox import DockerSandbox
from unittest.mock import Mock
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
            str(root / "auth.sqlite3"), auth_service=self.auth,
        )
        self.task = SandboxExecutionTask(
            task_id="exec-auth-1",
            image="registry.example/yuki@sha256:" + "a" * 64,
            argv=("python", "--version"),
            workspace=self.tmp.name,
        )


    def test_approval_store_requires_shared_auth_database(self):
        with self.assertRaisesRegex(ValueError, "share the authentication database"):
            SQLiteSandboxExecutionApprovalStore(
                str(Path(self.tmp.name) / "separate.sqlite3"), auth_service=self.auth,
            )

    def test_approval_is_persisted_and_verified_after_store_reopen(self):
        approval = self.store.approve(self.task, session_token=self.session.token)
        reopened = SQLiteSandboxExecutionApprovalStore(
            str(Path(self.tmp.name) / "auth.sqlite3"), auth_service=self.auth,
        )
        self.assertTrue(reopened.verify(approval, self.task))
        self.assertFalse(reopened.verify(approval, SandboxExecutionTask(
            task_id=self.task.task_id, image=self.task.image,
            argv=("python", "-c", "print(1)"), workspace=self.task.workspace,
        )))


    def test_approval_can_be_consumed_only_once(self):
        approval = self.store.approve(self.task, session_token=self.session.token)
        self.assertTrue(self.store.consume(approval, self.task))
        self.assertFalse(self.store.verify(approval, self.task))
        self.assertFalse(self.store.consume(approval, self.task))
        self.assertFalse(self.store.revoke(approval.approval_id, session_token=self.session.token))

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


    def test_worker_composition_rechecks_persisted_approval_and_writes_durable_audit(self):
        approval = self.store.approve(self.task, session_token=self.session.token)
        engine = DurableTaskEngine(":memory:")
        self.addCleanup(engine.close)
        goal = engine.create_goal()
        log = SQLiteApprovalAuditLog(str(Path(self.tmp.name) / "worker-audit.sqlite3"))
        writer = SandboxExecutionAuditWriter(log)
        sandbox = DockerSandbox(
            self.tmp.name, allowed_images=(self.task.image,), enabled=True,
            approver=lambda _reason: True,
        )
        sandbox.run = Mock(return_value={"returncode": 0, "stdout": "ok", "stderr": ""})
        worker = DurableSandboxExecutionWorker.from_trusted_stores(
            engine, worker_id="sandbox-worker", sandbox=sandbox,
            approval_store=self.store, audit_writer=writer,
        )
        queued = worker.submit(goal, self.task, approval, idempotency_key="durable-auth-approval")
        result = worker.run_once()
        self.assertEqual(result["state"], "succeeded")
        self.assertFalse(self.store.verify(approval, self.task))
        self.assertTrue(log.verify_chain())
        self.assertEqual(
            [item["event_type"] for item in reversed(log.list_events())],
            ["execution_started", "executed"],
        )




    def test_worker_marks_unconfirmed_queue_commit_as_unknown(self):
        approval = self.store.approve(self.task, session_token=self.session.token)
        engine = DurableTaskEngine(":memory:")
        self.addCleanup(engine.close)
        goal = engine.create_goal()
        log = SQLiteApprovalAuditLog(str(Path(self.tmp.name) / "commit-audit.sqlite3"))
        writer = SandboxExecutionAuditWriter(log)
        sandbox = DockerSandbox(
            self.tmp.name, allowed_images=(self.task.image,), enabled=True,
            approver=lambda _reason: True,
        )
        sandbox.run = Mock(return_value={"returncode": 0, "stdout": "ok", "stderr": ""})
        worker = DurableSandboxExecutionWorker.from_trusted_stores(
            engine, worker_id="sandbox-worker", sandbox=sandbox,
            approval_store=self.store, audit_writer=writer,
        )
        queued = worker.submit(goal, self.task, approval, idempotency_key="unknown-queue-commit")
        engine.complete = Mock(side_effect=RuntimeError("database unavailable"))
        with self.assertRaises(ExecutionOutcomeUnknown):
            worker.run_once()
        failed = engine.get_task(queued["id"])
        self.assertEqual(failed["state"], "failed")
        self.assertIn("manual reconciliation", failed["last_error"])

    def test_worker_recovers_when_queue_commit_succeeded_before_connection_error(self):
        approval = self.store.approve(self.task, session_token=self.session.token)
        engine = DurableTaskEngine(":memory:")
        self.addCleanup(engine.close)
        goal = engine.create_goal()
        log = SQLiteApprovalAuditLog(str(Path(self.tmp.name) / "commit-recovery-audit.sqlite3"))
        writer = SandboxExecutionAuditWriter(log)
        sandbox = DockerSandbox(
            self.tmp.name, allowed_images=(self.task.image,), enabled=True,
            approver=lambda _reason: True,
        )
        sandbox.run = Mock(return_value={"returncode": 0, "stdout": "ok", "stderr": ""})
        worker = DurableSandboxExecutionWorker.from_trusted_stores(
            engine, worker_id="sandbox-worker", sandbox=sandbox,
            approval_store=self.store, audit_writer=writer,
        )
        queued = worker.submit(goal, self.task, approval, idempotency_key="committed-before-error")
        original_complete = engine.complete
        def commit_then_raise(*args, **kwargs):
            original_complete(*args, **kwargs)
            raise RuntimeError("connection lost after commit")
        engine.complete = commit_then_raise
        result = worker.run_once()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(engine.get_task(queued["id"])["state"], "succeeded")

    def test_worker_persists_unknown_outcome_as_terminal_non_retryable_state(self):
        approval = self.store.approve(self.task, session_token=self.session.token)
        engine = DurableTaskEngine(":memory:")
        self.addCleanup(engine.close)
        goal = engine.create_goal()
        log = SQLiteApprovalAuditLog(str(Path(self.tmp.name) / "unknown-audit.sqlite3"))
        writer = SandboxExecutionAuditWriter(log)
        sandbox = DockerSandbox(
            self.tmp.name, allowed_images=(self.task.image,), enabled=True,
            approver=lambda _reason: True,
        )
        sandbox.run = Mock(side_effect=TimeoutError("docker response lost"))
        worker = DurableSandboxExecutionWorker.from_trusted_stores(
            engine, worker_id="sandbox-worker", sandbox=sandbox,
            approval_store=self.store, audit_writer=writer,
        )
        queued = worker.submit(goal, self.task, approval, idempotency_key="unknown-worker-outcome")
        with self.assertRaises(ExecutionOutcomeUnknown):
            worker.run_once()
        failed = engine.get_task(queued["id"])
        self.assertEqual(failed["state"], "failed")
        self.assertIn("manual reconciliation", failed["last_error"])
        self.assertTrue(any(
            event["event_type"] == "task.outcome_unknown"
            for event in engine.list_events(goal)
        ))
        self.assertIsNone(worker.run_once())

    def test_completion_audit_failure_is_recorded_as_unknown_not_retried(self):
        approval = self.store.approve(self.task, session_token=self.session.token)
        sandbox = DockerSandbox(
            self.tmp.name, allowed_images=(self.task.image,), enabled=True,
            approver=lambda _reason: True,
        )
        sandbox.run = Mock(return_value={"returncode": 0, "stdout": "ok", "stderr": ""})
        events = []
        def audit(event):
            events.append(event["event"])
            return event["event"] != "execution.completed"
        with self.assertRaises(ExecutionOutcomeUnknown):
            execute_approved_task(
                self.task, approval, sandbox=sandbox,
                verify_approval=self.store.verify, consume_approval=self.store.consume,
                audit=audit,
            )
        self.assertEqual(events, [
            "execution.started", "execution.completed", "execution.unknown",
        ])
        self.assertFalse(self.store.verify(approval, self.task))


    def test_docker_dispatch_exception_is_a_non_replayable_unknown_outcome(self):
        approval = self.store.approve(self.task, session_token=self.session.token)
        sandbox = DockerSandbox(
            self.tmp.name, allowed_images=(self.task.image,), enabled=True,
            approver=lambda _reason: True,
        )
        sandbox.run = Mock(side_effect=TimeoutError("docker client timed out"))
        events = []
        with self.assertRaises(ExecutionOutcomeUnknown):
            execute_approved_task(
                self.task, approval, sandbox=sandbox,
                verify_approval=self.store.verify, consume_approval=self.store.consume,
                audit=lambda event: events.append(event["event"]) or True,
            )
        self.assertEqual(events, ["execution.started", "execution.unknown"])
        self.assertFalse(self.store.verify(approval, self.task))

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
