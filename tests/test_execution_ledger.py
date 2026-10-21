import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from core.execution_ledger import SQLiteExecutionLedger


class SQLiteExecutionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "executions.sqlite3")
        self.ledger = SQLiteExecutionLedger(self.path)
        self.action = {"skill_id": "notes.write", "arguments": {"text": "hello"}}

    def test_begin_persists_intent_and_same_key_is_idempotent(self):
        first, created = self.ledger.begin("request-1", self.action)
        second, created_again = SQLiteExecutionLedger(self.path).begin("request-1", self.action)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.state, "prepared")
        self.assertEqual(second.state, "prepared")
        self.assertEqual(first.action_hash, second.action_hash)

    def test_same_key_cannot_be_reused_for_different_action(self):
        self.ledger.begin("request-1", self.action)
        with self.assertRaisesRegex(ValueError, "different action"):
            self.ledger.begin("request-1", {"skill_id": "notes.delete", "arguments": {}})

    def test_transition_state_machine_and_result_persistence(self):
        self.ledger.begin("request-1", self.action)
        self.ledger.transition("request-1", "running")
        result = {"ok": True, "record_id": "n-1"}
        completed = self.ledger.transition("request-1", "completed", result=result)
        self.assertEqual(completed.state, "completed")
        self.assertEqual(json.loads(completed.result_json), result)
        with self.assertRaisesRegex(ValueError, "Invalid execution transition"):
            self.ledger.transition("request-1", "running")

    def test_uncertain_executions_are_visible_for_reconciliation(self):
        self.ledger.begin("request-1", self.action)
        self.ledger.transition("request-1", "running")
        self.ledger.transition("request-1", "uncertain", result={"reason": "worker crashed"})
        pending = self.ledger.list_reconciliation_required()
        self.assertEqual([item.idempotency_key for item in pending], ["request-1"])
        self.assertEqual(pending[0].state, "uncertain")

    def test_prepared_records_are_visible_for_reconciliation(self):
        self.ledger.begin("request-prepared", self.action)
        records = self.ledger.list_reconciliation_required()
        self.assertEqual([record.idempotency_key for record in records], ["request-prepared"])
        self.assertEqual(records[0].state, "prepared")

    def test_reconciliation_results_use_key_as_stable_tiebreaker(self):
        self.ledger.begin("request-z", self.action)
        self.ledger.begin("request-a", self.action)
        # Force an identical timestamp so this test specifically verifies the
        # documented secondary sort key, independent of machine clock resolution.
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE action_execution_ledger SET updated_at = ?",
                ("2026-01-01T00:00:00+00:00",),
            )
        records = self.ledger.list_reconciliation_required()
        self.assertEqual(
            [record.idempotency_key for record in records],
            ["request-a", "request-z"],
        )

    def test_two_instances_cannot_create_duplicate_intent(self):
        second = SQLiteExecutionLedger(self.path)
        barrier = threading.Barrier(3)
        results = []
        errors = []
        lock = threading.Lock()

        def begin(ledger):
            try:
                barrier.wait()
                value = ledger.begin("request-race", self.action)
                with lock:
                    results.append(value[1])
            except Exception as exc:
                with lock:
                    errors.append(exc)

        workers = [
            threading.Thread(target=begin, args=(self.ledger,)),
            threading.Thread(target=begin, args=(second,)),
        ]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=5)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)


if __name__ == "__main__":
    unittest.main()
