import tempfile
import unittest
from pathlib import Path

from core.task_checkpoint_store import SQLiteTaskCheckpointStore


class SQLiteTaskCheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SQLiteTaskCheckpointStore(str(Path(self.temp.name) / "tasks.sqlite3"))

    def test_checkpoint_persists_and_advances_revision(self):
        first = self.store.create("task-1", {"goal": "build", "completed_steps": ["inspect"], "pending_steps": ["test"]})
        self.store.checkpoint("task-1", expected_revision=first.revision,
                              payload={"goal": "build", "completed_steps": ["inspect", "implement"], "pending_steps": ["test"]},
                              status="paused")
        reopened = SQLiteTaskCheckpointStore(str(Path(self.temp.name) / "tasks.sqlite3"))
        loaded = reopened.get("task-1")
        self.assertEqual(loaded.revision, 2)
        self.assertEqual(loaded.status, "paused")
        self.assertEqual(loaded.payload["completed_steps"], ["inspect", "implement"])

    def test_stale_revision_cannot_overwrite_newer_checkpoint(self):
        first = self.store.create("task-1", {"goal": "original"})
        self.store.checkpoint("task-1", expected_revision=first.revision, payload={"goal": "new"}, status="running")
        with self.assertRaisesRegex(RuntimeError, "Stale"):
            self.store.checkpoint("task-1", expected_revision=first.revision, payload={"goal": "stale"}, status="paused")
        self.assertEqual(self.store.get("task-1").payload["goal"], "new")

    def test_duplicate_id_does_not_overwrite_existing_work(self):
        self.store.create("task-1", {"goal": "original"})
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.create("task-1", {"goal": "replacement"})
        self.assertEqual(self.store.get("task-1").payload["goal"], "original")

    def test_rejects_non_json_and_oversized_payloads(self):
        with self.assertRaisesRegex(ValueError, "JSON-safe"):
            self.store.create("bad", {"value": object()})
        with self.assertRaisesRegex(ValueError, "256 KiB"):
            self.store.create("large", {"text": "x" * (257 * 1024)})

    def test_terminal_tasks_are_not_reopened(self):
        first = self.store.create("task-1", {"goal": "done"})
        done = self.store.checkpoint("task-1", expected_revision=first.revision, payload={"goal": "done"}, status="completed")
        with self.assertRaisesRegex(ValueError, "Terminal"):
            self.store.checkpoint("task-1", expected_revision=done.revision, payload={"goal": "repeat"}, status="running")

    def test_uncertain_task_cannot_resume_directly(self):
        first = self.store.create("task-1", {"goal": "resume"})
        uncertain = self.store.checkpoint("task-1", expected_revision=first.revision,
                                          payload={"goal": "resume"}, status="needs_reconciliation")
        with self.assertRaisesRegex(ValueError, "Reconcile"):
            self.store.checkpoint("task-1", expected_revision=uncertain.revision, payload=uncertain.payload, status="running")

    def test_lists_only_incomplete_tasks(self):
        self.store.create("task-1", {"goal": "active"})
        self.store.create("task-2", {"goal": "done"})
        self.store.checkpoint("task-2", expected_revision=1, payload={"goal": "done"}, status="completed")
        self.assertEqual([item.task_id for item in self.store.list_incomplete()], ["task-1"])


if __name__ == "__main__":
    unittest.main()
