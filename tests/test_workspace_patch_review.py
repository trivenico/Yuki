import hashlib
import unittest

from automation.approved_workspace_changes import FileChange
from automation.decision_review import EngineeringDecisionReview
from automation.specialist_quality import SynthesisQualityReport
from automation.workspace_change_proposal import prepare_workspace_change_proposal
from automation.workspace_patch_review import render_workspace_patch_review


def make_proposal(content="VALUE = 2\n"):
    quality = SynthesisQualityReport(
        plan_id="plan-1", structural_status="structurally_complete",
        expected_findings=1, received_findings=1, coverage_complete=True,
        findings=(), unresolved_subtasks=(),
    )
    review = EngineeringDecisionReview(
        plan_id="plan-1", quality=quality, disagreements=(),
        review_status="ready_for_human_decision", proposed_decision=None,
        requires_human_review=True, changes_authorized=False,
    )
    old = "VALUE = 1\n"
    change = FileChange("src/module.py", hashlib.sha256(old.encode()).hexdigest(), content)
    return prepare_workspace_change_proposal(
        review=review, decision_actor_id="owner",
        human_decision="Prepare this change for review.", changes=(change,),
    )


class WorkspacePatchReviewTests(unittest.TestCase):
    def test_renders_unified_diff_with_verified_digests(self):
        proposal = make_proposal()
        report = render_workspace_patch_review(
            proposal, {"src/module.py": "VALUE = 1\n"}
        )
        self.assertEqual(report.proposal_id, proposal.proposal_id)
        self.assertTrue(report.ready_for_human_review)
        self.assertFalse(report.applied)
        self.assertEqual(report.total_additions, 1)
        self.assertEqual(report.total_deletions, 1)
        self.assertIn("-VALUE = 1", report.files[0].unified_diff)
        self.assertIn("+VALUE = 2", report.files[0].unified_diff)

    def test_rejects_stale_base_content(self):
        with self.assertRaises(ValueError):
            render_workspace_patch_review(
                make_proposal(), {"src/module.py": "VALUE = 9\n"}
            )

    def test_requires_exact_base_file_set(self):
        with self.assertRaises(ValueError):
            render_workspace_patch_review(make_proposal(), {})

    def test_rejects_digest_tampering(self):
        proposal = make_proposal()
        object.__setattr__(proposal, "content_digest", "0" * 64)
        with self.assertRaises(ValueError):
            render_workspace_patch_review(
                proposal, {"src/module.py": "VALUE = 1\n"}
            )

    def test_rejects_binary_like_base_content(self):
        with self.assertRaises(ValueError):
            render_workspace_patch_review(
                make_proposal(), {"src/module.py": "VALUE = 1\x00"}
            )


if __name__ == "__main__":
    unittest.main()
