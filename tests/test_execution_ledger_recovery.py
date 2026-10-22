"""Crash-recovery and idempotency regression tests for the durable execution ledger."""
import unittest

from core.execution_ledger import SQLiteExecutionLedger


class ExecutionLedgerRecoveryTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._temporary_directory = tempfile.TemporaryDirectory()
        from pathlib import Path
        self.directory = Path(self._temporary_directory.name)

    def tearDown(self):
        self._temporary_directory.cleanup()

    def test_running_execution_survives_reopen_and_requires_reconciliation(self):
        database = self.directory / "execution.sqlite3"
        action = {"skill_id": "knowledge:index", "arguments": {"relative_path": "notes.md"}}
        first_process = SQLiteExecutionLedger(str(database))
        record, created = first_process.begin("approval-123", action)
        self.assertTrue(created)
        self.assertEqual(record.state, "prepared")
        first_process.transition("approval-123", "running")

        restarted_process = SQLiteExecutionLedger(str(database))
        persisted = restarted_process.get("approval-123")
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.state, "running")
        pending = restarted_process.list_reconciliation_required()
        self.assertEqual([item.idempotency_key for item in pending], ["approval-123"])

    def test_reusing_idempotency_key_never_resets_running_state(self):
        ledger = SQLiteExecutionLedger(str(self.directory / "execution.sqlite3"))
        action = {"skill_id": "reminder:add", "arguments": {"text": "review", "due_at": "2027-01-01T00:00:00+00:00"}}
        record, created = ledger.begin("approval-456", action)
        self.assertTrue(created)
        ledger.transition("approval-456", "running")
        existing, created_again = ledger.begin("approval-456", action)
        self.assertFalse(created_again)
        self.assertEqual(existing.state, "running")
        self.assertEqual(ledger.get("approval-456").state, "running")

    def test_idempotency_key_cannot_be_rebound_to_different_action(self):
        ledger = SQLiteExecutionLedger(str(self.directory / "execution.sqlite3"))
        ledger.begin("approval-789", {"skill_id": "knowledge:index", "arguments": {"relative_path": "a.md"}})
        with self.assertRaisesRegex(ValueError, "different action"):
            ledger.begin("approval-789", {"skill_id": "knowledge:index", "arguments": {"relative_path": "b.md"}})

    def test_completed_execution_is_not_listed_for_reconciliation(self):
        ledger = SQLiteExecutionLedger(str(self.directory / "execution.sqlite3"))
        action = {"skill_id": "knowledge:index", "arguments": {"relative_path": "notes.md"}}
        ledger.begin("approval-complete", action)
        ledger.transition("approval-complete", "running")
        ledger.transition("approval-complete", "completed", result={"ok": True})
        self.assertEqual(ledger.list_reconciliation_required(), [])


if __name__ == "__main__":
    unittest.main()
