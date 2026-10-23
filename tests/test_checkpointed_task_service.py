import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.task_checkpoint_store import SQLiteTaskCheckpointStore
from core.checkpointed_task_service import CheckpointedTaskService


class FakeTaskAgent:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def run(self, goal):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class CheckpointedTaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SQLiteTaskCheckpointStore(str(Path(self.temp.name) / "tasks.sqlite3"))

    def test_successful_agent_task_is_persisted_as_completed(self):
        agent = FakeTaskAgent(SimpleNamespace(ok=True, status="completed", message="done", steps=2))
        service = CheckpointedTaskService(self.store, agent)
        outcome = service.execute("build a feature")
        self.assertEqual(outcome.checkpoint.status, "completed")
        self.assertEqual(outcome.checkpoint.payload["goal"], "build a feature")
        self.assertEqual(outcome.checkpoint.payload["completed_steps"], [0])
        self.assertEqual(outcome.agent_status, "completed")
        self.assertEqual(agent.calls, 1)

    def test_failed_agent_task_requires_reconciliation_and_is_listed(self):
        agent = FakeTaskAgent(SimpleNamespace(ok=False, status="provider_error", message="provider unavailable", steps=0))
        service = CheckpointedTaskService(self.store, agent)
        outcome = service.execute("work")
        self.assertEqual(outcome.checkpoint.status, "needs_reconciliation")
        self.assertEqual(outcome.agent_status, "provider_error")
        self.assertEqual(len(service.list_incomplete()), 1)
        self.assertEqual(agent.calls, 1)

    def test_agent_exception_is_checkpointed_not_reported_as_success(self):
        agent = FakeTaskAgent(error=RuntimeError("interrupted"))
        service = CheckpointedTaskService(self.store, agent)
        outcome = service.execute("work")
        self.assertNotEqual(outcome.checkpoint.status, "completed")
        self.assertIsNotNone(outcome.error_type)
        self.assertEqual(len(service.list_incomplete()), 1)

    def test_invalid_agent_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "task_agent"):
            CheckpointedTaskService(self.store, object())


if __name__ == "__main__":
    unittest.main()
