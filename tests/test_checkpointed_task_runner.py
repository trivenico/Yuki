import tempfile
import unittest
from pathlib import Path

from core.task_checkpoint_store import SQLiteTaskCheckpointStore
from core.checkpointed_task_runner import CheckpointedTaskRunner


class CheckpointedTaskRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SQLiteTaskCheckpointStore(str(Path(self.temp.name) / "tasks.sqlite3"))
        self.runner = CheckpointedTaskRunner(self.store)

    def test_runs_steps_and_persists_each_completed_step(self):
        self.runner.create("task-1", "complete work", ["inspect", "implement", "test"])
        calls = []
        final = self.runner.run("task-1", lambda step, index: calls.append((step, index)) or {"step": step})
        self.assertEqual(calls, [("inspect", 0), ("implement", 1), ("test", 2)])
        self.assertEqual(final.status, "completed")
        self.assertEqual(final.payload["current_step"], 3)
        self.assertEqual(final.payload["completed_steps"], [0, 1, 2])
        self.assertEqual(len(final.payload["results"]), 3)

    def test_failed_step_is_not_automatically_retried(self):
        self.runner.create("task-1", "do work", ["first", "uncertain", "last"])
        calls = []
        def execute(step, index):
            calls.append(step)
            if step == "uncertain":
                raise RuntimeError("provider failed after request")
            return {"ok": True}
        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            self.runner.run("task-1", execute)
        self.assertEqual(calls, ["first", "uncertain"])
        saved = self.store.get("task-1")
        self.assertEqual(saved.status, "needs_reconciliation")
        self.assertEqual(saved.payload["completed_steps"], [0])
        with self.assertRaisesRegex(RuntimeError, "reconciliation"):
            self.runner.run("task-1", execute)
        self.assertEqual(calls, ["first", "uncertain"])

    def test_invalid_progress_is_rejected_before_step_execution(self):
        checkpoint = self.runner.create("task-1", "do work", ["one"])
        self.store.checkpoint("task-1", expected_revision=checkpoint.revision,
                              payload={"goal": "do work", "steps": ["one"],
                                       "completed_steps": [0], "current_step": 0, "results": []},
                              status="paused")
        called = []
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            self.runner.run("task-1", lambda step, index: called.append(step))
        self.assertEqual(called, [])

    def test_completed_task_is_not_replayed(self):
        self.runner.create("task-1", "do work", ["one"])
        calls = []
        self.runner.run("task-1", lambda step, index: calls.append(step) or "ok")
        again = self.runner.run("task-1", lambda step, index: calls.append(step) or "duplicate")
        self.assertEqual(again.status, "completed")
        self.assertEqual(calls, ["one"])

    def test_rejects_empty_plan(self):
        with self.assertRaisesRegex(ValueError, "1 to 100"):
            self.runner.create("task-1", "goal", [])


if __name__ == "__main__":
    unittest.main()
