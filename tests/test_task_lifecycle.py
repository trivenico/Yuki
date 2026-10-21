import tempfile
import unittest
from pathlib import Path

from core.task_lifecycle import (
    InvalidTransition, SQLiteTaskLifecycleStore, TaskConflict,
)


class SQLiteTaskLifecycleStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteTaskLifecycleStore(str(Path(self.tmp.name) / "tasks.sqlite3"))

    def create(self, key="request-1", payload=None):
        return self.store.create_task(
            idempotency_key=key, task_type="skill.invoke",
            payload={"skill_id": "notes.write"} if payload is None else payload,
        )

    def test_create_starts_pending_and_duplicate_key_is_idempotent(self):
        first = self.create()
        second = self.create()
        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual(first.state, "pending")
        self.assertEqual(second.version, 0)

    def test_idempotency_key_cannot_be_reused_for_different_payload(self):
        self.create()
        with self.assertRaises(TaskConflict):
            self.create(payload={"skill_id": "shell.exec"})

    def test_valid_lifecycle_and_persisted_result(self):
        task = self.create()
        task = self.store.transition(task.task_id, expected_state="pending",
                                     new_state="approved", expected_version=task.version)
        task = self.store.transition(task.task_id, expected_state="approved",
                                     new_state="running", expected_version=task.version)
        task = self.store.transition(task.task_id, expected_state="running",
                                     new_state="completed", expected_version=task.version,
                                     result={"ok": True})
        self.assertEqual(task.state, "completed")
        self.assertEqual(task.result, {"ok": True})
        self.assertEqual(task.version, 3)
        self.assertEqual(self.store.get_task(task.task_id), task)

    def test_invalid_transition_is_rejected(self):
        task = self.create()
        with self.assertRaises(InvalidTransition):
            self.store.transition(task.task_id, expected_state="pending", new_state="completed",
                                  result={"ok": True})

    def test_stale_version_is_rejected(self):
        task = self.create()
        approved = self.store.transition(task.task_id, expected_state="pending",
                                         new_state="approved", expected_version=task.version)
        with self.assertRaises(TaskConflict):
            self.store.transition(task.task_id, expected_state="pending", new_state="approved",
                                  expected_version=task.version)
        self.assertEqual(approved.version, 1)

    def test_interrupted_running_task_requires_recovery_not_retry(self):
        task = self.create()
        task = self.store.transition(task.task_id, expected_state="pending", new_state="approved")
        task = self.store.transition(task.task_id, expected_state="approved", new_state="running")
        self.assertEqual(self.store.mark_interrupted_tasks_for_recovery(), 1)
        recovered = self.store.get_task(task.task_id)
        self.assertEqual(recovered.state, "recovery_required")
        self.assertEqual(recovered.error_code, "interrupted_after_restart")
        self.assertEqual(self.store.mark_interrupted_tasks_for_recovery(), 0)

    def test_terminal_state_cannot_restart(self):
        task = self.create()
        task = self.store.transition(task.task_id, expected_state="pending", new_state="cancelled")
        with self.assertRaises(InvalidTransition):
            self.store.transition(task.task_id, expected_state="cancelled", new_state="running")

    def test_non_finite_and_oversized_payloads_are_rejected(self):
        with self.assertRaises(ValueError):
            self.create(payload={"number": float("nan")})
        with self.assertRaises(ValueError):
            self.create(payload={"text": "x" * (65 * 1024)})


if __name__ == "__main__":
    unittest.main()
