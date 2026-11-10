import unittest
from core.github_check_gate import (
    CheckGateError, GitHubCheck, PullRequestEvidence, evaluate_pull_request_checks,
)

SHA = "a" * 40
OTHER_SHA = "b" * 40

def evidence(**kwargs):
    base = dict(
        number=71, state="open", draft=False, head_sha=SHA, base_branch="main",
        checks=(GitHubCheck("unit", "completed", "success", SHA),),
        required_checks=("unit",),
    )
    base.update(kwargs)
    return PullRequestEvidence(**base)

class GitHubCheckGateTests(unittest.TestCase):
    def test_current_successful_required_check_passes(self):
        result = evaluate_pull_request_checks(evidence(), expected_head_sha=SHA)
        self.assertEqual(result.decision, "ready-for-required-checks")
        self.assertEqual(result.reasons, ())

    def test_missing_required_check_blocks(self):
        result = evaluate_pull_request_checks(evidence(checks=()), expected_head_sha=SHA)
        self.assertEqual(result.decision, "block")
        self.assertIn("required_check_missing:unit", result.reasons)

    def test_failure_and_skipped_required_check_block(self):
        for conclusion in ("failure", "skipped", "cancelled", None):
            status = "completed" if conclusion is not None else "in_progress"
            result = evaluate_pull_request_checks(
                evidence(checks=(GitHubCheck("unit", status, conclusion, SHA),)),
                expected_head_sha=SHA,
            )
            self.assertEqual(result.decision, "block", conclusion)

    def test_stale_check_sha_blocks(self):
        result = evaluate_pull_request_checks(
            evidence(checks=(GitHubCheck("unit", "completed", "success", OTHER_SHA),)),
            expected_head_sha=SHA,
        )
        self.assertEqual(result.decision, "block")
        self.assertIn("stale_check:unit", result.reasons)

    def test_stale_pr_head_blocks(self):
        result = evaluate_pull_request_checks(evidence(head_sha=OTHER_SHA), expected_head_sha=SHA)
        self.assertEqual(result.decision, "block")

    def test_draft_closed_and_human_approval_block(self):
        cases = (
            evidence(draft=True),
            evidence(state="closed"),
            evidence(human_approval_required=True, human_approved=False),
        )
        for item in cases:
            with self.subTest(item=item):
                self.assertEqual(evaluate_pull_request_checks(item, expected_head_sha=SHA).decision, "block")

    def test_conflicting_duplicate_checks_block(self):
        item = evidence(checks=(
            GitHubCheck("unit", "completed", "success", SHA),
            GitHubCheck("unit", "completed", "failure", SHA),
        ))
        result = evaluate_pull_request_checks(item, expected_head_sha=SHA)
        self.assertEqual(result.decision, "block")
        self.assertIn("conflicting_duplicate_check:unit", result.reasons)

    def test_invalid_sha_rejected(self):
        with self.assertRaises(CheckGateError):
            evaluate_pull_request_checks(evidence(head_sha="bad"), expected_head_sha=SHA)

if __name__ == "__main__":
    unittest.main()
