import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from automation.independent_verification import VerificationCheck, VerificationReport, sha256_artifact
from automation.verification_evidence_store import VerificationEvidenceStore, VerificationConflict

NAMES = ("acceptance_criteria", "tests", "security", "architecture", "claims_and_provenance")


def make_report(status="passed"):
    return VerificationReport(
        artifact_sha256=sha256_artifact(b"reviewed source snapshot"),
        author_agent_id="implementation-agent",
        checks=tuple(VerificationCheck(
            name=name, reviewer_agent_id="reviewer-" + name,
            status=status, evidence_reference="evidence://" + name,
            summary="Reviewed " + name,
        ) for name in NAMES),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


class VerificationEvidenceStoreTests(unittest.TestCase):
    def test_record_and_read_survives_store_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "verification.sqlite3"
            first = VerificationEvidenceStore(path)
            result = first.record(make_report())
            second = VerificationEvidenceStore(path)
            stored = second.get(result["report_id"])
            self.assertIsNotNone(stored)
            self.assertTrue(stored["decision"]["accepted"])

    def test_exact_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VerificationEvidenceStore(Path(tmp) / "verification.sqlite3")
            report = make_report()
            first = store.record(report)
            second = store.record(report)
            self.assertFalse(first["reused"])
            self.assertTrue(second["reused"])
            self.assertEqual(first["report_id"], second["report_id"])

    def test_failed_report_is_persisted_but_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VerificationEvidenceStore(Path(tmp) / "verification.sqlite3")
            result = store.record(make_report(status="not_run"))
            self.assertFalse(result["decision"]["accepted"])

    def test_artifact_history_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VerificationEvidenceStore(Path(tmp) / "verification.sqlite3")
            report = make_report()
            store.record(report)
            self.assertEqual(len(store.list_for_artifact(report.artifact_sha256)), 1)
            with self.assertRaises(ValueError):
                store.list_for_artifact(report.artifact_sha256, limit=0)


if __name__ == "__main__":
    unittest.main()
