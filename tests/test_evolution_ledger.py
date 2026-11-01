import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from automation.evolution_ledger import EvolutionLedger, EvolutionLedgerError


class EvolutionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "evolution.sqlite3"
        self.ledger = EvolutionLedger(self.db_path)

    def append(self, event_type="proposal_created", request_id="upgrade-1", **kwargs):
        return self.ledger.append(
            event_type=event_type, request_id=request_id,
            actor_id="alice@example.invalid", **kwargs,
        )

    def test_append_redacts_actor_and_builds_verifiable_chain(self):
        event = self.append(details={"component": "planner", "status": "proposed"})
        self.assertEqual(event["sequence"], 1)
        self.assertNotIn("alice@example.invalid", str(event))
        self.assertEqual(len(event["actor_hash"]), 64)
        self.assertTrue(self.ledger.verify_chain())
        self.assertEqual(self.ledger.head()["event_hash"], event["event_hash"])

    def test_event_history_can_be_filtered_by_request(self):
        self.append(request_id="request-a")
        self.append("plan_created", request_id="request-b")
        events = self.ledger.list_events(request_id="request-a")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["request_id"], "request-a")

    def test_concurrent_appends_have_unique_ordered_sequences(self):
        barrier = threading.Barrier(8)
        errors = []
        def worker(index):
            try:
                barrier.wait(timeout=5)
                self.ledger.append(
                    event_type="execution_started", request_id="parallel",
                    actor_id=f"worker-{index}", details={"component": "executor"},
                )
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        events = self.ledger.list_events(request_id="parallel", limit=100)
        self.assertEqual(sorted(e["sequence"] for e in events), list(range(1, 9)))
        self.assertTrue(self.ledger.verify_chain())

    def test_tampered_event_is_detected(self):
        self.append(details={"status": "proposed"})
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE evolution_events SET event_type='execution_completed' WHERE sequence=1")
        self.assertFalse(self.ledger.verify_chain())

    def test_details_are_strictly_allowlisted(self):
        with self.assertRaisesRegex(ValueError, "not allowlisted"):
            self.append(details={"prompt": "private content"})
        with self.assertRaises(ValueError):
            self.append(details={"failed_count": -1})
        with self.assertRaises(ValueError):
            self.append(details={"component": ["unexpected", "object"]})
        with self.assertRaises(ValueError):
            self.append(details={"component": True})
        with self.assertRaises(ValueError):
            self.append(details={"test_count": True})
        self.assertEqual(self.ledger.head()["sequence"], 0)

    def test_event_type_and_request_identifier_are_validated(self):
        with self.assertRaises(ValueError):
            self.append("arbitrary_event")
        with self.assertRaises(ValueError):
            self.append(request_id="../unsafe")

    def test_empty_ledger_has_valid_zero_head(self):
        self.assertTrue(self.ledger.verify_chain())
        self.assertEqual(self.ledger.head()["sequence"], 0)

    def test_persistence_survives_reopen(self):
        self.append(event_type="plan_created", details={"component": "planner"})
        reopened = EvolutionLedger(self.db_path)
        self.assertTrue(reopened.verify_chain())
        self.assertEqual(reopened.head()["sequence"], 1)


if __name__ == "__main__":
    unittest.main()
