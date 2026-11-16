"""Tests for approval-bound isolated execution task dispatch."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from core.durable_task_engine import DurableTaskEngine
from automation.execution_task import (
    DurableSandboxExecutionWorker, EXECUTION_TASK_TYPE,
    ExecutionApproval,
    ExecutionOutcomeUnknown,
    SandboxExecutionTask,
    execute_approved_task,
)
from hackback.sandbox import DockerSandbox


class ApprovedExecutionTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = "registry.example/yuki@sha256:" + "b" * 64
        self.task = SandboxExecutionTask(
            task_id="exec-001",
            image=self.image,
            argv=("python", "-c", "print('safe')"),
            workspace=self.tmp.name,
        )
        self.approval = ExecutionApproval(
            approval_id="approval-001",
            actor_id="operator-1",
            task_id=self.task.task_id,
            task_fingerprint=self.task.fingerprint(),
            approved=True,
        )
        self.sandbox = DockerSandbox(
            self.tmp.name, allowed_images=(self.image,), enabled=True,
            approver=lambda _reason: True,
        )

    def test_task_fingerprint_binds_every_execution_field(self):
        changed = SandboxExecutionTask(
            task_id=self.task.task_id, image=self.image,
            argv=("python", "-c", "print('different')"),
            workspace=self.tmp.name,
        )
        self.assertNotEqual(self.task.fingerprint(), changed.fingerprint())

    def test_execution_requires_trusted_approval_verification(self):
        run = Mock(return_value={"returncode": 0, "stdout": "ok", "stderr": ""})
        self.sandbox.run = run
        audit = Mock()
        with self.assertRaisesRegex(PermissionError, "not verified"):
            execute_approved_task(
                self.task, self.approval, sandbox=self.sandbox,
                verify_approval=lambda _approval, _task: False, audit=audit,
            )
        run.assert_not_called()
        audit.assert_not_called()

    def test_approval_cannot_be_replayed_for_modified_task(self):
        changed = SandboxExecutionTask(
            task_id=self.task.task_id, image=self.image,
            argv=("python", "--version"), workspace=self.tmp.name,
        )
        run = Mock()
        with self.assertRaisesRegex(PermissionError, "exact execution task"):
            execute_approved_task(
                changed, self.approval, sandbox=self.sandbox,
                verify_approval=lambda _approval, _task: True, audit=Mock(),
            )
        run.assert_not_called()

    def test_verified_task_is_audited_and_dispatched_with_bounded_options(self):
        result = {"returncode": 0, "stdout": "ok", "stderr": ""}
        run = Mock(return_value=result)
        self.sandbox.run = run
        events = []
        returned = execute_approved_task(
            self.task, self.approval, sandbox=self.sandbox,
            verify_approval=lambda approval, task: approval.task_id == task.task_id,
            audit=events.append,
        )
        self.assertEqual(returned, result)
        run.assert_called_once_with(
            self.image, self.task.argv, timeout=30,
            write_workspace=False, max_output_bytes=24_000,
        )
        self.assertEqual(
            [event["event"] for event in events],
            ["execution.started", "execution.completed"],
        )
        self.assertEqual(events[0]["task_fingerprint"], self.task.fingerprint())

    def test_audit_start_failure_prevents_execution(self):
        run = Mock()
        self.sandbox.run = run
        def fail_audit(_event):
            raise OSError("audit unavailable")
        with self.assertRaisesRegex(OSError, "audit unavailable"):
            execute_approved_task(
                self.task, self.approval, sandbox=self.sandbox,
                verify_approval=lambda _approval, _task: True, audit=fail_audit,
            )
        run.assert_not_called()

    def test_workspace_must_match_configured_sandbox(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        run = Mock()
        self.sandbox.run = run
        changed = SandboxExecutionTask(
            task_id=self.task.task_id, image=self.image, argv=self.task.argv,
            workspace=str(Path(other.name)),
        )
        approval = ExecutionApproval(
            approval_id=self.approval.approval_id, actor_id=self.approval.actor_id,
            task_id=changed.task_id, task_fingerprint=changed.fingerprint(), approved=True,
        )
        with self.assertRaisesRegex(PermissionError, "workspace does not match"):
            execute_approved_task(
                changed, approval, sandbox=self.sandbox,
                verify_approval=lambda _approval, _task: True, audit=Mock(),
            )
        run.assert_not_called()

    def test_task_bounds_and_boolean_integer_trap(self):
        with self.assertRaises(ValueError):
            SandboxExecutionTask(
                task_id="exec-002", image=self.image, argv=("python",),
                workspace=self.tmp.name, timeout_seconds=True,
            ).validate()
        with self.assertRaises(ValueError):
            SandboxExecutionTask(
                task_id="exec-003", image=self.image, argv=("python",),
                workspace=self.tmp.name, max_output_bytes=0,
            ).validate()

    def test_durable_worker_claims_only_execution_tasks_and_persists_result(self):
        engine = DurableTaskEngine(":memory:")
        self.addCleanup(engine.close)
        goal = engine.create_goal()
        text_task = engine.submit(
            goal, "specialist.text", {"text": "hello"}, idempotency_key="text-task",
        )
        worker = DurableSandboxExecutionWorker(
            engine, worker_id="sandbox-worker", sandbox=self.sandbox,
            verify_approval=lambda approval, task: (
                approval.task_fingerprint == task.fingerprint()
            ),
            audit=lambda _event: True,
        )
        run = Mock(return_value={"returncode": 0, "stdout": "ok", "stderr": ""})
        self.sandbox.run = run
        queued = worker.submit(
            goal, self.task, self.approval, idempotency_key="execution-task",
        )
        self.assertEqual(queued["task_type"], EXECUTION_TASK_TYPE)
        completed = worker.run_once()
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(completed["result"]["stdout"], "ok")
        self.assertEqual(engine.get_task(text_task["id"])["state"], "queued")
        run.assert_called_once()

    def test_durable_worker_never_retries_side_effecting_execution(self):
        engine = DurableTaskEngine(":memory:")
        self.addCleanup(engine.close)
        goal = engine.create_goal()
        worker = DurableSandboxExecutionWorker(
            engine, worker_id="sandbox-worker", sandbox=self.sandbox,
            verify_approval=lambda _approval, _task: True,
            audit=lambda _event: True,
        )
        run = Mock(side_effect=RuntimeError("simulated sandbox failure"))
        self.sandbox.run = run
        queued = worker.submit(
            goal, self.task, self.approval, idempotency_key="single-attempt",
        )
        with self.assertRaises(ExecutionOutcomeUnknown) as raised:
            worker.run_once()
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertIn("simulated sandbox failure", str(raised.exception.__cause__))
        failed = engine.get_task(queued["id"])
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["attempts"], 1)
        self.assertIsNone(engine.claim("another-worker", task_types=(EXECUTION_TASK_TYPE,)))

    def test_durable_worker_cancellation_is_queued_only(self):
        engine = DurableTaskEngine(":memory:")
        self.addCleanup(engine.close)
        goal = engine.create_goal()
        worker = DurableSandboxExecutionWorker(
            engine, worker_id="sandbox-worker", sandbox=self.sandbox,
            verify_approval=lambda _approval, _task: True,
            audit=lambda _event: True,
        )
        queued = worker.submit(
            goal, self.task, self.approval, idempotency_key="cancel-before-run",
        )
        cancelled = worker.cancel(queued["id"])
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertIsNone(worker.run_once())

    def test_durable_worker_rejects_workspace_mismatch_before_enqueue(self):
        engine = DurableTaskEngine(":memory:")
        self.addCleanup(engine.close)
        goal = engine.create_goal()
        worker = DurableSandboxExecutionWorker(
            engine, worker_id="sandbox-worker", sandbox=self.sandbox,
            verify_approval=lambda _approval, _task: True,
            audit=lambda _event: True,
        )
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        changed = SandboxExecutionTask(
            task_id="exec-other", image=self.image, argv=self.task.argv,
            workspace=elsewhere.name,
        )
        approval = ExecutionApproval(
            approval_id="approval-other", actor_id="operator-1",
            task_id=changed.task_id, task_fingerprint=changed.fingerprint(),
            approved=True,
        )
        with self.assertRaisesRegex(PermissionError, "workspace does not match"):
            worker.submit(goal, changed, approval, idempotency_key="wrong-workspace")

    def test_execution_worker_cannot_cancel_other_task_types(self):
        engine = DurableTaskEngine(":memory:")
        self.addCleanup(engine.close)
        goal = engine.create_goal()
        text_task = engine.submit(
            goal, "specialist.text", {}, idempotency_key="unrelated-task",
        )
        worker = DurableSandboxExecutionWorker(
            engine, worker_id="sandbox-worker", sandbox=self.sandbox,
            verify_approval=lambda _approval, _task: True,
            audit=lambda _event: True,
        )
        with self.assertRaisesRegex(PermissionError, "different task type"):
            worker.cancel(text_task["id"])
        self.assertEqual(engine.get_task(text_task["id"])["state"], "queued")


if __name__ == "__main__":
    unittest.main()
