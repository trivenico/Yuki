import tempfile
import unittest
from pathlib import Path

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from integrations.github_actions_dispatch import (
    GitHubActionsDispatchError,
    GitHubActionsDispatchLedger,
    GitHubActionsTaskDispatcher,
)


class GitHubActionsDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowRunStore(str(Path(self.tmp.name) / "tasks.sqlite3"))
        self.grants = {"task:create", "task:read", "task:ci:dispatch"}
        self.tasks = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task: actor == "alice" and action in self.grants,
        )
        self.task = self.tasks.enqueue(
            actor_id="alice", idempotency_key="ci-task-1", name="ci-check",
            steps=[{"name": "review", "action": "emit", "argument": "inspect",
                    "requires_approval": False}],
        )
        self.ledger = GitHubActionsDispatchLedger(str(Path(self.tmp.name) / "dispatch.sqlite3"))
        self.calls = []

    def dispatcher(self, binding=None, callback=None):
        return GitHubActionsTaskDispatcher(
            repository="acme/yuki", workflow_id=12345,
            allowed_branches={"main"}, task_runtime=self.tasks, ledger=self.ledger,
            source_binding_resolver=lambda owner, task: binding or (self.task.version, "a" * 40, "main"),
            workflow_dispatch=callback or (lambda **kwargs: self.calls.append(kwargs)),
        )

    def test_dispatch_pins_task_version_source_and_run_name_inputs(self):
        result = self.dispatcher().dispatch(
            actor_id="alice", owner_id="alice", task_id=self.task.task_id,
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.source_sha, "a" * 40)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["ref"], "main")
        self.assertEqual(self.calls[0]["inputs"], {
            "yuki_task_id": self.task.task_id,
            "yuki_task_version": str(self.task.version),
            "yuki_source_sha": "a" * 40,
        })
        self.assertEqual(self.calls[0]["idempotency_key"], result.idempotency_key)

    def test_same_task_attempt_cannot_be_dispatched_twice(self):
        dispatcher = self.dispatcher()
        dispatcher.dispatch(actor_id="alice", owner_id="alice", task_id=self.task.task_id)
        with self.assertRaises(GitHubActionsDispatchError):
            dispatcher.dispatch(actor_id="alice", owner_id="alice", task_id=self.task.task_id)
        self.assertEqual(len(self.calls), 1)

    def test_source_version_or_branch_mismatch_fails_closed(self):
        for binding in (
            (self.task.version + 1, "a" * 40, "main"),
            (self.task.version, "not-a-sha", "main"),
            (self.task.version, "a" * 40, "untrusted"),
        ):
            with self.subTest(binding=binding):
                with self.assertRaises(GitHubActionsDispatchError):
                    self.dispatcher(binding=binding).dispatch(
                        actor_id="alice", owner_id="alice", task_id=self.task.task_id,
                    )
        self.assertEqual(self.calls, [])

    def test_non_owner_and_missing_permission_are_rejected(self):
        with self.assertRaises(GitHubActionsDispatchError):
            self.dispatcher().dispatch(
                actor_id="bob", owner_id="alice", task_id=self.task.task_id,
            )
        self.grants.remove("task:ci:dispatch")
        with self.assertRaises(GitHubActionsDispatchError):
            self.dispatcher().dispatch(
                actor_id="alice", owner_id="alice", task_id=self.task.task_id,
            )
        self.assertEqual(self.calls, [])

    def test_unknown_network_outcome_is_fenced_from_automatic_retry(self):
        def uncertain(**kwargs):
            raise TimeoutError("request timed out after send")
        dispatcher = self.dispatcher(callback=uncertain)
        with self.assertRaisesRegex(GitHubActionsDispatchError, "outcome is unknown"):
            dispatcher.dispatch(actor_id="alice", owner_id="alice", task_id=self.task.task_id)
        with self.assertRaisesRegex(GitHubActionsDispatchError, "already has a dispatch reservation"):
            dispatcher.dispatch(actor_id="alice", owner_id="alice", task_id=self.task.task_id)


if __name__ == "__main__":
    unittest.main()
