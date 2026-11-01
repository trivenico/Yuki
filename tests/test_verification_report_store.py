import tempfile
import unittest
from pathlib import Path

from automation.independent_verification import (
    IndependentVerificationEngine, VerificationFinding,
)
from automation.verification_report_store import (
    VerificationReportStore, VerificationReportStoreError,
)


class Reviewer:
    def is_available(self):
        return True
    def generate(self, prompt):
        return '{"status":"approved","summary":"Evidence reviewed.","findings":[]}'


class VerificationReportStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = VerificationReportStore(
            str(Path(self.temp.name) / "reports.sqlite3")
        )
        self.report = IndependentVerificationEngine(
            Reviewer(), reviewer_id="reviewer", author_id="builder"
        ).review(
            requirements=["Validate permissions."],
            artifact={"change": "authorization guard"},
            evidence=[{"kind": "test", "result": "passed"}],
            tests_passed=True,
        )

    def test_persists_and_reads_digest_verified_report(self):
        saved = self.store.save(workflow_id="eng_1", report=self.report)
        loaded = self.store.get(saved["report_id"])
        self.assertEqual(loaded["report_sha256"], saved["report_sha256"])
        self.assertEqual(loaded["report"]["status"], "approved")
        self.assertEqual(len(self.store.list_for_workflow("eng_1")), 1)

    def test_idempotent_save_returns_existing_report(self):
        first = self.store.save(workflow_id="eng_1", report=self.report)
        second = self.store.save(workflow_id="eng_1", report=self.report)
        self.assertEqual(first["report_id"], second["report_id"])

    def test_conflicting_report_for_same_artifact_is_rejected(self):
        self.store.save(workflow_id="eng_1", report=self.report)
        different = type(self.report)(
            status="changes_requested", reviewer_id="reviewer", author_id="builder",
            artifact_sha256=self.report.artifact_sha256, findings=(),
            summary="Different assessment.", human_approval_required=True,
            advisory_only=True,
        )
        with self.assertRaises(VerificationReportStoreError):
            self.store.save(workflow_id="eng_1", report=different)

    def test_limits_are_validated(self):
        with self.assertRaises(ValueError):
            self.store.list_for_workflow("eng_1", limit=101)


if __name__ == "__main__":
    unittest.main()
