import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from automation.independent_verification import VerificationCheck, VerificationReport, sha256_artifact
from automation.verification_evidence_store import VerificationEvidenceStore
from automation.verification_workflow import VerificationWorkflow, VerificationWorkflowError

NAMES = ("acceptance_criteria", "tests", "security", "architecture", "claims_and_provenance")


def make_report(content=b"artifact bytes", author="builder"):
    return VerificationReport(
        artifact_sha256=sha256_artifact(content),
        author_agent_id=author,
        checks=tuple(VerificationCheck(
            name=name, reviewer_agent_id="reviewer-" + name,
            status="passed", evidence_reference="evidence://" + name,
            summary="Independently reviewed " + name,
        ) for name in NAMES),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


class VerificationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.evidence = VerificationEvidenceStore(root / "evidence.sqlite3")
        self.workflow = VerificationWorkflow(
            root / "workflow.sqlite3", self.evidence,
            reviewer_authorize=lambda actor, action, artifact: actor == "review-lead" and action == "verification:submit",
        )
        self.artifact = self.workflow.record_artifact(
            plan_id="plan-1", subtask_id="subtask-1", task_id="task-1",
            author_agent_id="builder", content=b"artifact bytes",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_matching_independent_report_links_and_verifies_artifact(self):
        report = make_report()
        saved = self.evidence.record(report)
        linked = self.workflow.link_report(
            artifact_id=self.artifact.artifact_id, report_id=saved["report_id"],
            reviewer_actor_id="review-lead", created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.assertTrue(linked["accepted"])
        self.assertTrue(self.workflow.get_status(self.artifact.artifact_id)["verified"])

    def test_digest_mismatch_rejected(self):
        saved = self.evidence.record(make_report(content=b"different bytes"))
        with self.assertRaisesRegex(VerificationWorkflowError, "digest_mismatch"):
            self.workflow.link_report(
                artifact_id=self.artifact.artifact_id, report_id=saved["report_id"],
                reviewer_actor_id="review-lead", created_at="2026-11-07T00:00:00+00:00",
            )

    def test_unauthorized_reviewer_rejected(self):
        saved = self.evidence.record(make_report())
        with self.assertRaises(PermissionError):
            self.workflow.link_report(
                artifact_id=self.artifact.artifact_id, report_id=saved["report_id"],
                reviewer_actor_id="unknown", created_at="2026-11-07T00:00:00+00:00",
            )

    def test_self_review_author_rejected_by_report_validator(self):
        checks = tuple(VerificationCheck(
            name=name, reviewer_agent_id="builder", status="passed",
            evidence_reference="evidence://" + name, summary="Self review",
        ) for name in NAMES)
        with self.assertRaisesRegex(ValueError, "author_cannot_verify"):
            self.evidence.record(VerificationReport(
                artifact_sha256=self.artifact.artifact_sha256,
                author_agent_id="builder", checks=checks,
                created_at=datetime.now(timezone.utc).isoformat(),
            ))

    def test_failed_report_does_not_verify_artifact(self):
        report = make_report()
        failed = VerificationReport(
            report.artifact_sha256, report.author_agent_id,
            tuple(VerificationCheck(
                name=c.name, reviewer_agent_id=c.reviewer_agent_id,
                status=("failed" if c.name == "tests" else c.status),
                evidence_reference=c.evidence_reference, summary=c.summary,
            ) for c in report.checks), report.created_at,
        )
        saved = self.evidence.record(failed)
        self.workflow.link_report(
            artifact_id=self.artifact.artifact_id, report_id=saved["report_id"],
            reviewer_actor_id="review-lead", created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.assertFalse(self.workflow.get_status(self.artifact.artifact_id)["verified"])


if __name__ == "__main__":
    unittest.main()
