import tempfile
import unittest
from pathlib import Path

from core.task_checkpoint_store import SQLiteTaskCheckpointStore
from core.task_startup_recovery import recover_task_checkpoints


class TaskStartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = SQLiteTaskCheckpointStore(str(self.root / "task_checkpoints.sqlite3"))

    def test_stale_running_tasks_are_quarantined_not_replayed(self):
        created = self.store.create("running-task", {"goal": "external operation"})
        self.store.checkpoint("running-task", expected_revision=created.revision,
                              payload=created.payload, status="running")
        report = recover_task_checkpoints(self.root, stale_after_seconds=0)
        self.assertEqual(report.stale_running, ("running-task",))
        self.assertEqual(report.needs_reconciliation, ("running-task",))
        saved = self.store.get("running-task")
        self.assertEqual(saved.status, "needs_reconciliation")
        self.assertEqual(saved.revision, 3)

    def test_pending_and_paused_tasks_are_reported_without_mutation(self):
        self.store.create("pending-task", {"goal": "pending"})
        paused = self.store.create("paused-task", {"goal": "paused"})
        self.store.checkpoint("paused-task", expected_revision=paused.revision,
                              payload=paused.payload, status="paused")
        report = recover_task_checkpoints(self.root)
        self.assertEqual(report.pending, ("pending-task",))
        self.assertEqual(report.paused, ("paused-task",))
        self.assertEqual(self.store.get("paused-task").status, "paused")

    def test_missing_database_is_noop(self):
        empty = Path(self.temp.name) / "empty"
        empty.mkdir()
        report = recover_task_checkpoints(empty)
        self.assertEqual(report.total_incomplete, 0)
        self.assertFalse(report.truncated)

    def test_rejects_invalid_configuration(self):
        with self.assertRaisesRegex(ValueError, "database name"):
            recover_task_checkpoints(self.root, database_name="other.sqlite3")
        with self.assertRaisesRegex(ValueError, "stale_after_seconds"):
            recover_task_checkpoints(self.root, stale_after_seconds=-1)

    def test_refuses_symlink_database(self):
        target = self.root / "real.sqlite3"
        SQLiteTaskCheckpointStore(str(target))
        link = self.root / "task_checkpoints.sqlite3"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not permitted")
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            recover_task_checkpoints(self.root)


if __name__ == "__main__":
    unittest.main()
