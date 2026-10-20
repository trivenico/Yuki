"""Durable task lifecycle and interruption-recovery tests."""

import tempfile
import unittest
from pathlib import Path

from core.task_runtime import DurableTaskRunner
from core.task_store import TaskStore


class FakeResult:
    def __init__(self, ok, message="result", status="completed", steps=1):
        self.ok = ok
        self.message = message
        self.status = status
        self.steps = steps


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "tasks.sqlite3"
        self.store = TaskStore(self.path)

    def test_create_and_transition_persist(self):
        task = self.store.create("summarize files")
        self.assertEqual(task["status"], "queued")
        running = self.store.transition(task["task_id"], "running")
        self.assertEqual(running["status"], "running")
        completed = self.store.transition(
            task["task_id"], "completed", result={"ok": True, "message": "done"}
        )
        self.assertEqual(completed["result"], {"ok": True, "message": "done"})
        reopened = TaskStore(self.path).get(task["task_id"])
        self.assertEqual(reopened["status"], "completed")
        self.assertEqual(reopened["result"]["ok"], True)

    def test_rejects_invalid_goals_and_oversized_goals(self):
        for goal in ("", "   ", None, "x" * 4001):
            with self.subTest(goal_type=type(goal).__name__), self.assertRaises(ValueError):
                self.store.create(goal)

    def test_rejects_invalid_transition_and_terminal_replay(self):
        task = self.store.create("task")
        with self.assertRaisesRegex(ValueError, "invalid task transition"):
            self.store.transition(task["task_id"], "completed")
        self.store.transition(task["task_id"], "running")
        self.store.transition(task["task_id"], "completed", result={"ok": True})
        with self.assertRaisesRegex(ValueError, "invalid task transition"):
            self.store.transition(task["task_id"], "running")

    def test_missing_task_is_rejected(self):
        with self.assertRaisesRegex(KeyError, "task not found"):
            self.store.transition("missing", "running")

    def test_result_must_be_json_and_bounded(self):
        task = self.store.create("task")
        self.store.transition(task["task_id"], "running")
        with self.assertRaisesRegex(ValueError, "JSON-serializable"):
            self.store.transition(task["task_id"], "failed", result=object())
        with self.assertRaisesRegex(ValueError, "storage limit"):
            self.store.transition(task["task_id"], "failed", result={"data": "x" * 17000})
        self.assertEqual(self.store.get(task["task_id"])["status"], "running")

    def test_list_limit_validation_and_order(self):
        first = self.store.create("first")
        second = self.store.create("second")
        self.assertEqual([t["task_id"] for t in self.store.list_tasks(limit=2)], [second["task_id"], first["task_id"]])
        for limit in (0, 501, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.list_tasks(limit=limit)

    def test_recovery_marks_unfinished_tasks_without_replay(self):
        queued = self.store.create("queued task")
        running = self.store.create("running task")
        self.store.transition(running["task_id"], "running")
        completed = self.store.create("completed task")
        self.store.transition(completed["task_id"], "running")
        self.store.transition(completed["task_id"], "completed", result={"ok": True})
        self.assertEqual(self.store.recover_interrupted(), 2)
        self.assertEqual(self.store.get(queued["task_id"])["status"], "interrupted")
        self.assertEqual(self.store.get(running["task_id"])["status"], "interrupted")
        self.assertEqual(self.store.get(completed["task_id"])["status"], "completed")
        self.assertEqual(self.store.recover_interrupted(), 0)

    def test_durable_runner_records_success_and_failure(self):
        runner = DurableTaskRunner(self.store)
        task_id, result = runner.run("good task", lambda goal: FakeResult(True, "done"))
        self.assertTrue(result.ok)
        self.assertEqual(self.store.get(task_id)["status"], "completed")
        task_id, result = runner.run("failed task", lambda goal: FakeResult(False, "denied", "approval_denied"))
        self.assertFalse(result.ok)
        self.assertEqual(self.store.get(task_id)["status"], "failed")
        self.assertEqual(self.store.get(task_id)["result"]["status"], "approval_denied")

    def test_durable_runner_marks_keyboard_interrupt_and_never_replays(self):
        runner = DurableTaskRunner(self.store)

        def interrupted(goal):
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            runner.run("interrupt me", interrupted)
        tasks = self.store.list_tasks()
        self.assertEqual(tasks[0]["status"], "interrupted")
        self.assertEqual(self.store.recover_interrupted(), 0)

    def test_durable_runner_marks_exception_interrupted(self):
        runner = DurableTaskRunner(self.store)

        def crash(goal):
            raise RuntimeError("failure")

        with self.assertRaisesRegex(RuntimeError, "failure"):
            runner.run("crash", crash)
        self.assertEqual(self.store.list_tasks()[0]["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
