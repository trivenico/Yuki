import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from evaluation.evolution_candidates import EvolutionCandidate
from evaluation.execution_receipts import create_execution_manifest, create_execution_receipt
from evaluation.execution_receipt_store import CandidateExecutionReceiptStore


class CandidateExecutionReceiptStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = str(Path(self.temp.name) / "receipts.sqlite3")
        self.store = CandidateExecutionReceiptStore(self.database)
        candidate = EvolutionCandidate(
            candidate_id="candidate-a", owner_id="owner-a", kind="prompt",
            title="Title", rationale="Reason", expected_outcome="Outcome",
            evidence_refs=(), risk_tags=(), parent_digest=None,
            payload_digest="a" * 64, created_at="2026-11-27T00:00:00+00:00",
            state="accepted_for_evaluation", revision=1,
        )
        self.manifest = create_execution_manifest(
            candidate=candidate, artifact=b"artifact", benchmark_id="benchmark",
            benchmark_version=1, benchmark_digest="b" * 64,
            input_manifest=b'{"inputs":[]}', sandbox_image="sha256:" + "c" * 64,
            runtime_identity="runtime/1", runner_version="runner/1",
            attempt_id="attempt-a", created_at="2026-11-27T01:00:00+00:00",
        )
        self.receipt = create_execution_receipt(
            manifest=self.manifest, output=b"output", exit_code=0, timed_out=False,
            started_at="2026-11-27T01:00:01+00:00",
            completed_at="2026-11-27T01:00:02+00:00",
        )

    def test_round_trip_is_owner_scoped_and_integrity_checked(self):
        self.store.record(owner_id="owner-a", manifest=self.manifest, receipt=self.receipt)
        manifest, receipt = self.store.get(owner_id="owner-a", attempt_id="attempt-a")
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(receipt, self.receipt)
        with self.assertRaises(KeyError):
            self.store.get(owner_id="owner-b", attempt_id="attempt-a")

    def test_rejects_duplicate_attempt_and_owner_mismatch(self):
        self.store.record(owner_id="owner-a", manifest=self.manifest, receipt=self.receipt)
        with self.assertRaises(ValueError):
            self.store.record(owner_id="owner-a", manifest=self.manifest, receipt=self.receipt)
        with self.assertRaises(ValueError):
            self.store.record(owner_id="owner-b", manifest=self.manifest, receipt=self.receipt)

    def test_database_tampering_is_detected(self):
        self.store.record(owner_id="owner-a", manifest=self.manifest, receipt=self.receipt)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "UPDATE candidate_execution_receipts SET receipt_digest=? WHERE attempt_id=?",
                ("d" * 64, "attempt-a"),
            )
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "integrity"):
            self.store.get(owner_id="owner-a", attempt_id="attempt-a")

    def test_append_only_triggers_reject_mutation(self):
        self.store.record(owner_id="owner-a", manifest=self.manifest, receipt=self.receipt)
        with closing(sqlite3.connect(self.database)) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM candidate_execution_receipts WHERE attempt_id='attempt-a'")


if __name__ == "__main__":
    unittest.main()
