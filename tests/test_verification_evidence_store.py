import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from automation.independent_verification import VerificationCheck, VerificationReport, sha256_artifact
from automation.verification_evidence_store import VerificationEvidenceStore, VerificationConflict

def allow_test_reviewers(actor_id, action, artifact_sha256):
    return actor_id.startswith(("implementation-", "reviewer-")) and action in {
        "verification:submit", "verification:review"
    } and len(artifact_sha256) == 64


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
    def test_fails_closed_without_trusted_reviewer_authorizer(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VerificationEvidenceStore(Path(tmp) / "verification.sqlite3")
            with self.assertRaisesRegex(Exception, "trusted_reviewer_authorizer_not_configured"):
                store.record(make_report())

    def test_author_and_each_reviewer_are_authorized_for_exact_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def authorize(actor_id, action, digest):
                calls.append((actor_id, action, digest))
                return True
            report = make_report()
            store = VerificationEvidenceStore(
                Path(tmp) / "verification.sqlite3",
                reviewer_authorizer=authorize,
            )
            store.record(report)
            self.assertEqual(len(calls), 1 + len(NAMES))
            self.assertEqual(calls[0], (
                report.author_agent_id, "verification:submit", report.artifact_sha256
            ))
            self.assertEqual(
                set(calls[1:]),
                {
                    (check.reviewer_agent_id, "verification:review", report.artifact_sha256)
                    for check in report.checks
                },
            )

    def test_rejects_unregistered_reviewer_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VerificationEvidenceStore(
                Path(tmp) / "verification.sqlite3",
                reviewer_authorizer=lambda actor, action, digest: actor != "reviewer-security",
            )
            with self.assertRaises(PermissionError):
                store.record(make_report())

    def test_record_and_read_survives_store_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "verification.sqlite3"
            first = VerificationEvidenceStore(path, reviewer_authorizer=allow_test_reviewers)
            result = first.record(make_report())
            second = VerificationEvidenceStore(path)
            stored = second.get(result["report_id"])
            self.assertIsNotNone(stored)
            self.assertTrue(stored["decision"]["accepted"])

    def test_exact_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VerificationEvidenceStore(Path(tmp) / "verification.sqlite3", reviewer_authorizer=allow_test_reviewers)
            report = make_report()
            first = store.record(report)
            second = store.record(report)
            self.assertFalse(first["reused"])
            self.assertTrue(second["reused"])
            self.assertEqual(first["report_id"], second["report_id"])

    def test_failed_report_is_persisted_but_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VerificationEvidenceStore(
                Path(tmp) / "verification.sqlite3", reviewer_authorizer=allow_test_reviewers
            )
            result = store.record(make_report(status="not_run"))
            self.assertFalse(result["decision"]["accepted"])

    def test_artifact_history_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VerificationEvidenceStore(
                Path(tmp) / "verification.sqlite3", reviewer_authorizer=allow_test_reviewers
            )
            report = make_report()
            store.record(report)
            self.assertEqual(len(store.list_for_artifact(report.artifact_sha256)), 1)
            with self.assertRaises(ValueError):
                store.list_for_artifact(report.artifact_sha256, limit=0)


if __name__ == "__main__":
    unittest.main()
