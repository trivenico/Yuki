import tempfile
import unittest
from pathlib import Path

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore


class TaskIdempotencyIsolationTests(unittest.TestCase):
    def test_same_idempotency_key_is_scoped_to_actor(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowRunStore(str(Path(tmp) / "tasks.sqlite3"))
            runtime = TaskRuntime(
                store,
                authorize=lambda actor, action, task: action == "task:create" and actor in {"alice", "bob"},
            )
            steps = [{"name": "private", "action": "emit", "argument": "owner-specific"}]
            alice = runtime.enqueue(actor_id="alice", idempotency_key="same-key", name="alice-task", steps=steps)
            bob = runtime.enqueue(actor_id="bob", idempotency_key="same-key", name="bob-task", steps=steps)
            self.assertNotEqual(alice.task_id, bob.task_id)
            self.assertEqual(alice.name, "alice-task")
            self.assertEqual(bob.name, "bob-task")

    def test_same_actor_retry_remains_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowRunStore(str(Path(tmp) / "tasks.sqlite3"))
            runtime = TaskRuntime(
                store,
                authorize=lambda actor, action, task: action == "task:create" and actor == "alice",
            )
            steps = [{"name": "private", "action": "emit", "argument": "same"}]
            first = runtime.enqueue(actor_id="alice", idempotency_key="retry", name="task", steps=steps)
            replay = runtime.enqueue(actor_id="alice", idempotency_key="retry", name="task", steps=steps)
            self.assertEqual(first.task_id, replay.task_id)


if __name__ == "__main__":
    unittest.main()
