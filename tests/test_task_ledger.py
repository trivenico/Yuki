import tempfile
import unittest
from pathlib import Path

from core.task_ledger import TASK_STATUSES, TaskLedger


class TaskLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tasks.sqlite3"
        self.ledger = TaskLedger(self.path)

    def test_create_and_read_task(self):
        task = self.ledger.create("Prepare project report", detail="Collect status", metadata={"source": "desktop"})
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["title"], "Prepare project report")
        self.assertEqual(task["metadata"], {"source": "desktop"})
        self.assertEqual(self.ledger.get(task["id"])["id"], task["id"])

    def test_valid_lifecycle_and_terminal_state(self):
        task = self.ledger.create("Run tests")
        self.assertEqual(self.ledger.transition(task["id"], "running")["status"], "running")
        self.assertEqual(self.ledger.transition(task["id"], "awaiting_approval")["status"], "awaiting_approval")
        self.assertEqual(self.ledger.transition(task["id"], "running")["status"], "running")
        completed = self.ledger.transition(task["id"], "completed", result="Tests passed")
        self.assertEqual(completed["result"], "Tests passed")
        with self.assertRaisesRegex(ValueError, "transition"):
            self.ledger.transition(task["id"], "running")

    def test_failure_and_cancel_are_terminal(self):
        failed = self.ledger.create("Fail task")
        self.ledger.transition(failed["id"], "failed", error_code="provider_error")
        with self.assertRaisesRegex(ValueError, "transition"):
            self.ledger.transition(failed["id"], "completed")
        cancelled = self.ledger.create("Cancel task")
        self.assertEqual(self.ledger.cancel(cancelled["id"])["status"], "cancelled")
        with self.assertRaisesRegex(ValueError, "transition"):
            self.ledger.cancel(cancelled["id"])

    def test_tasks_survive_reopening_store(self):
        task = self.ledger.create("Persistent task")
        reopened = TaskLedger(self.path)
        self.assertEqual(reopened.get(task["id"])["title"], "Persistent task")

    def test_listing_filters_terminal_tasks_and_respects_limit(self):
        active = self.ledger.create("Active")
        done = self.ledger.create("Done")
        self.ledger.transition(done["id"], "cancelled")
        self.assertEqual([item["id"] for item in self.ledger.list(include_terminal=False)], [active["id"]])
        self.assertEqual(len(self.ledger.list(limit=1)), 1)
        with self.assertRaisesRegex(ValueError, "between 1 and 200"):
            self.ledger.list(limit=0)

    def test_rejects_invalid_input_and_unknown_ids(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.ledger.create(" ")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            self.ledger.transition("x", "invisible")
        with self.assertRaisesRegex(KeyError, "not found"):
            self.ledger.transition("missing", "running")
        with self.assertRaisesRegex(ValueError, "JSON-serializable"):
            self.ledger.create("Bad metadata", metadata={"value": object()})

    def test_status_set_is_explicit(self):
        self.assertEqual(TASK_STATUSES, {
            "queued", "running", "awaiting_approval", "completed", "failed", "cancelled"
        })


if __name__ == "__main__":
    unittest.main()
