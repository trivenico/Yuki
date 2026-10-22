import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.task_checkpoint_store import SQLiteTaskCheckpointStore
from core.checkpointed_task_service import CheckpointedTaskService


class FailedAgent:
    def run(self, goal):
        return SimpleNamespace(ok=False, status="interrupted", message="unknown outcome", steps=1)


class TaskReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SQLiteTaskCheckpointStore(str(Path(self.temp.name) / "tasks.sqlite3"))
        self.service = CheckpointedTaskService(self.store, FailedAgent())
        self.outcome = self.service.execute("do an operation")
        self.checkpoint = self.outcome.checkpoint

    def test_confirmed_not_executed_pauses_but_does_not_resume(self):
        resolved = self.service.reconcile(
            self.checkpoint.task_id, expected_revision=self.checkpoint.revision,
            disposition="confirmed_not_executed", evidence="Verified provider audit: request not accepted.",
        )
        self.assertEqual(resolved.status, "paused")
        self.assertEqual(resolved.payload["reconciliation_history"][-1]["disposition"], "confirmed_not_executed")
        self.assertEqual(resolved.payload["reconciliation_history"][-1]["operator"], "local-owner")

    def test_confirmed_completed_marks_terminal(self):
        resolved = self.service.reconcile(
            self.checkpoint.task_id, expected_revision=self.checkpoint.revision,
            disposition="confirmed_completed", evidence="Verified target state.",
        )
        self.assertEqual(resolved.status, "completed")
        with self.assertRaisesRegex(ValueError, "Only tasks needing"):
            self.service.reconcile(
                self.checkpoint.task_id, expected_revision=resolved.revision,
                disposition="unresolved", evidence="follow-up",
            )

    def test_unresolved_remains_blocked(self):
        resolved = self.service.reconcile(
            self.checkpoint.task_id, expected_revision=self.checkpoint.revision,
            disposition="unresolved", evidence="Provider state cannot be determined.",
        )
        self.assertEqual(resolved.status, "needs_reconciliation")

    def test_stale_revision_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Stale"):
            self.service.reconcile(
                self.checkpoint.task_id, expected_revision=self.checkpoint.revision + 1,
                disposition="confirmed_not_executed", evidence="audit checked",
            )

    def test_requires_nonempty_evidence_and_valid_disposition(self):
        with self.assertRaisesRegex(ValueError, "Evidence"):
            self.service.reconcile(
                self.checkpoint.task_id, expected_revision=self.checkpoint.revision,
                disposition="confirmed_not_executed", evidence=" ",
            )
        with self.assertRaisesRegex(ValueError, "Unsupported disposition"):
            self.service.reconcile(
                self.checkpoint.task_id, expected_revision=self.checkpoint.revision,
                disposition="retry_now", evidence="operator wants retry",
            )


if __name__ == "__main__":
    unittest.main()
