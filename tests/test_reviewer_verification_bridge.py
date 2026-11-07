import tempfile
import unittest
from pathlib import Path

from automation.reviewer_contracts import build_review_result
from automation.reviewer_team import ReviewerAssignment, ReviewerTeamPlan
from automation.reviewer_workflow import build_review_tasks
from automation.reviewer_ledger import ReviewerTaskLedger
from automation.reviewer_signing import HMACReviewerSigner
from automation.reviewer_verification_bridge import (
    ReviewerVerificationBridge, ReviewerVerificationBridgeError,
)
from automation.verification_evidence_store import VerificationEvidenceStore


class ReviewerVerificationBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.digest = "b" * 64
        self.signer = HMACReviewerSigner(key_id="review-key-v1", key=b"z" * 32)
        self.ledger = ReviewerTaskLedger(
            root / "ledger.sqlite",
            authorize=lambda actor, action, digest: digest == self.digest,
            signer_resolver=lambda actor, key_id: self.signer if key_id in (None, "review-key-v1") else None,
        )
        plan = ReviewerTeamPlan(
            artifact_sha256=self.digest, author_agent_id="builder",
            assignments=(ReviewerAssignment("security", "security-reviewer"),),
        )
        self.task = build_review_tasks(
            plan,
            requirements_by_check={"security": ("Inspect authorization",)},
            evidence_references_by_check={"security": ("snapshot://source",)},
        )[0]
        self.ledger.create_tasks(actor_id="supervisor", tasks=(self.task,))
        self.store = VerificationEvidenceStore(
            root / "verification.sqlite",
            reviewer_authorizer=lambda actor, action, digest: (
                digest == self.digest and action in {"verification:submit", "verification:review"}
            ),
        )
        self.bridge = ReviewerVerificationBridge(
            self.ledger, self.store,
            evidence_reference_validator=lambda reference, digest: (
                digest == self.digest and isinstance(reference, str)
                and reference.startswith("snapshot://")
            ),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_submitted_result_becomes_artifact_bound_verification_check(self):
        self.ledger.claim(actor_id="security-reviewer", task_id=self.task.task_id, expected_version=0)
        result = build_review_result(self.task, status="passed", summary="Security review complete.")
        self.ledger.submit(
            actor_id="security-reviewer", task_id=self.task.task_id,
            expected_version=1, result=result,
        )
        saved = self.bridge.submit_report(
            actor_id="auditor", artifact_sha256=self.digest,
            author_agent_id="builder", task_ids=(self.task.task_id,),
            required_checks=("security",),
        )
        self.assertFalse(saved["reused"])
        self.assertFalse(saved["decision"]["accepted"])
        self.assertEqual(saved["decision"]["missing_checks"], (
            "acceptance_criteria", "architecture", "claims_and_provenance", "tests"
        ))

    def test_unsubmitted_task_is_rejected(self):
        with self.assertRaisesRegex(ReviewerVerificationBridgeError, "not_submitted"):
            self.bridge.submit_report(
                actor_id="auditor", artifact_sha256=self.digest,
                author_agent_id="builder", task_ids=(self.task.task_id,),
                required_checks=("security",),
            )

    def test_artifact_mismatch_is_rejected(self):
        self.ledger.claim(actor_id="security-reviewer", task_id=self.task.task_id, expected_version=0)
        result = build_review_result(self.task, status="passed", summary="Reviewed.")
        self.ledger.submit(actor_id="security-reviewer", task_id=self.task.task_id,
                           expected_version=1, result=result)
        with self.assertRaisesRegex(ReviewerVerificationBridgeError, "digest_mismatch"):
            self.bridge.submit_report(
                actor_id="auditor", artifact_sha256="c" * 64,
                author_agent_id="builder", task_ids=(self.task.task_id,),
                required_checks=("security",),
            )

    def test_evidence_reference_requires_trusted_validation(self):
        self.ledger.claim(actor_id="security-reviewer", task_id=self.task.task_id, expected_version=0)
        result = build_review_result(self.task, status="passed", summary="Reviewed.")
        self.ledger.submit(actor_id="security-reviewer", task_id=self.task.task_id,
                           expected_version=1, result=result)
        bridge = ReviewerVerificationBridge(
            self.ledger, self.store,
            evidence_reference_validator=lambda reference, digest: False,
        )
        with self.assertRaisesRegex(ReviewerVerificationBridgeError, "evidence_reference_invalid"):
            bridge.submit_report(
                actor_id="auditor", artifact_sha256=self.digest,
                author_agent_id="builder", task_ids=(self.task.task_id,),
                required_checks=("security",),
            )

    def test_wrong_author_is_rejected(self):
        self.ledger.claim(actor_id="security-reviewer", task_id=self.task.task_id, expected_version=0)
        result = build_review_result(self.task, status="passed", summary="Reviewed.")
        self.ledger.submit(actor_id="security-reviewer", task_id=self.task.task_id,
                           expected_version=1, result=result)
        with self.assertRaisesRegex(ReviewerVerificationBridgeError, "author_identity_mismatch"):
            self.bridge.submit_report(
                actor_id="auditor", artifact_sha256=self.digest,
                author_agent_id="someone-else", task_ids=(self.task.task_id,),
                required_checks=("security",),
            )


if __name__ == "__main__":
    unittest.main()
