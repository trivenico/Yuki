import hashlib
import unittest

from automation.approved_workspace_changes import FileChange
from automation.decision_review import EngineeringDecisionReview
from automation.specialist_quality import SynthesisQualityReport
from automation.workspace_change_proposal import prepare_workspace_change_proposal


def ready_review():
    quality = SynthesisQualityReport(
        plan_id="plan-1",
        structural_status="structurally_complete",
        expected_findings=1,
        received_findings=1,
        coverage_complete=True,
        findings=(),
        unresolved_subtasks=(),
    )
    return EngineeringDecisionReview(
        plan_id="plan-1",
        quality=quality,
        disagreements=(),
        review_status="ready_for_human_decision",
        proposed_decision=None,
        requires_human_review=True,
        changes_authorized=False,
    )


class WorkspaceChangeProposalTests(unittest.TestCase):
    def change(self, path="src/module.py", content="VALUE = 2\n"):
        return FileChange(
            path=path,
            expected_sha256=hashlib.sha256(b"VALUE = 1\n").hexdigest(),
            content=content,
        )

    def test_prepares_inert_digest_bound_proposal(self):
        proposal = prepare_workspace_change_proposal(
            review=ready_review(),
            decision_actor_id="owner",
            human_decision="Approve the bounded refactor proposal for implementation review.",
            changes=(self.change(),),
        )
        self.assertEqual(proposal.plan_id, "plan-1")
        self.assertEqual(proposal.changes, (self.change(),))
        self.assertEqual(len(proposal.content_digest), 64)
        self.assertTrue(proposal.requires_explicit_apply_approval)
        self.assertFalse(proposal.applied)

    def test_blocked_review_cannot_produce_proposal(self):
        review = ready_review()
        blocked = EngineeringDecisionReview(
            plan_id=review.plan_id,
            quality=review.quality,
            disagreements=(),
            review_status="blocked_for_review",
            proposed_decision=None,
        )
        with self.assertRaises(ValueError):
            prepare_workspace_change_proposal(
                review=blocked, decision_actor_id="owner",
                human_decision="Proceed", changes=(self.change(),),
            )

    def test_missing_human_decision_rejected(self):
        with self.assertRaises(ValueError):
            prepare_workspace_change_proposal(
                review=ready_review(), decision_actor_id="owner",
                human_decision=" ", changes=(self.change(),),
            )

    def test_duplicate_paths_rejected(self):
        with self.assertRaises(ValueError):
            prepare_workspace_change_proposal(
                review=ready_review(), decision_actor_id="owner",
                human_decision="Proceed with proposal",
                changes=(self.change(), self.change()),
            )

    def test_malformed_digest_rejected(self):
        invalid = FileChange("src/module.py", "not-a-digest", "VALUE = 2\n")
        with self.assertRaises(ValueError):
            prepare_workspace_change_proposal(
                review=ready_review(), decision_actor_id="owner",
                human_decision="Proceed with proposal", changes=(invalid,),
            )

    def test_rejects_traversal_and_secret_paths(self):
        for path in ("../outside.py", "/tmp/outside.py", ".env", "config/private_key.pem", "src\\\\module.py"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    prepare_workspace_change_proposal(
                        review=ready_review(), decision_actor_id="owner",
                        human_decision="Proceed with proposal",
                        changes=(self.change(path=path),),
                    )

    def test_rejects_mismatched_quality_plan(self):
        review = ready_review()
        quality = SynthesisQualityReport(
            plan_id="different-plan", structural_status="structurally_complete",
            expected_findings=1, received_findings=1, coverage_complete=True,
            findings=(), unresolved_subtasks=(),
        )
        mismatched = EngineeringDecisionReview(
            plan_id=review.plan_id, quality=quality, disagreements=(),
            review_status="ready_for_human_decision", proposed_decision=None,
            requires_human_review=True, changes_authorized=False,
        )
        with self.assertRaises(ValueError):
            prepare_workspace_change_proposal(
                review=mismatched, decision_actor_id="owner",
                human_decision="Proceed", changes=(self.change(),),
            )

    def test_rejects_combined_patch_over_limit(self):
        large = self.change(content="x" * (600 * 1024))
        with self.assertRaises(ValueError):
            prepare_workspace_change_proposal(
                review=ready_review(), decision_actor_id="owner",
                human_decision="Proceed", changes=(large, large.__class__(
                    path="src/other.py", expected_sha256=large.expected_sha256,
                    content="y" * (500 * 1024),
                )),
            )


if __name__ == "__main__":
    unittest.main()
