import tempfile
import unittest
from pathlib import Path

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore, WorkflowConflict
from ui.task_controls import approve_queued_task


class TaskApprovalControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.grants = set()
        self.runtime = TaskRuntime(
            WorkflowRunStore(str(Path(self.temp.name) / "tasks.sqlite3")),
            authorize=lambda actor, action, _task: actor == "operator-1" and action in {
                "task:create", "task:approve",
            },
        )
        self.task = self.runtime.enqueue(
            actor_id="operator-1",
            idempotency_key="approval-test",
            name="agent-research-brief",
            steps=[{
                "name": "research-brief", "action": "emit",
                "argument": "Summarize this request",
                "requires_approval": True,
            }],
        )

    def authorize(self, actor_id, action):
        return actor_id == "operator-1" and action in self.grants

    def test_requires_exact_approval_confirmation(self):
        self.grants.add("task:approve")
        with self.assertRaises(ValueError):
            approve_queued_task(
                self.runtime, actor_id="operator-1", session_id="session-1",
                authorize=self.authorize, task_id=self.task.task_id,
                expected_version=self.task.version, confirmation="approve",
            )
        self.assertIsNone(self.runtime.store.get(self.task.task_id).approved_by)

    def test_denied_or_unavailable_authorizer_fails_closed(self):
        with self.assertRaises(PermissionError):
            approve_queued_task(
                self.runtime, actor_id="operator-1", session_id="session-1",
                authorize=self.authorize, task_id=self.task.task_id,
                expected_version=self.task.version, confirmation="APPROVE",
            )
        with self.assertRaises(PermissionError):
            approve_queued_task(
                self.runtime, actor_id="operator-1", session_id="session-1",
                authorize=lambda *_args: (_ for _ in ()).throw(RuntimeError()),
                task_id=self.task.task_id, expected_version=self.task.version,
                confirmation="APPROVE",
            )
        self.assertIsNone(self.runtime.store.get(self.task.task_id).approved_by)

    def test_approval_is_explicit_and_version_checked(self):
        self.grants.add("task:approve")
        result = approve_queued_task(
            self.runtime, actor_id="operator-1", session_id="session-1",
            authorize=self.authorize, task_id=self.task.task_id,
            expected_version=self.task.version, confirmation="APPROVE",
        )
        current = self.runtime.store.get(self.task.task_id)
        self.assertIn("Approved task", result)
        self.assertEqual(current.approved_by, "operator-1")
        self.assertEqual(current.state, "queued")
        with self.assertRaises(WorkflowConflict):
            approve_queued_task(
                self.runtime, actor_id="operator-1", session_id="session-1",
                authorize=self.authorize, task_id=self.task.task_id,
                expected_version=self.task.version, confirmation="APPROVE",
            )

    def test_approval_does_not_run_the_task(self):
        self.grants.add("task:approve")
        approve_queued_task(
            self.runtime, actor_id="operator-1", session_id="session-1",
            authorize=self.authorize, task_id=self.task.task_id,
            expected_version=self.task.version, confirmation="APPROVE",
        )
        self.assertEqual(self.runtime.store.get(self.task.task_id).state, "queued")


if __name__ == "__main__":
    unittest.main()
