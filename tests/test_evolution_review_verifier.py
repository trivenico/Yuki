import unittest

from automation.evolution_review_verifier import (
    CheckResult,
    EvolutionReviewError,
    EvolutionReviewVerifier,
    ReviewEvidence,
    ReviewVerdict,
    VerificationReport,
)


class EvolutionReviewVerifierTests(unittest.TestCase):
    def setUp(self):
        self.verifier = EvolutionReviewVerifier()
        self.sha = "a" * 40
        self.names = (
            "unit_tests", "integration_tests", "static_analysis",
            "dependency_audit", "secret_scan", "security_tests",
        )

    def review(self, **kwargs):
        values = dict(
            reviewer_id="independent-reviewer", author_id="change-author",
            commit_sha=self.sha, verdict=ReviewVerdict.APPROVE,
            findings=(), rationale_code="review_complete",
        )
        values.update(kwargs)
        return ReviewEvidence(**values)

    def report(self, *, failed=(), sha=None):
        commit = sha or self.sha
        return VerificationReport(
            commit_sha=commit,
            checks=tuple(
                CheckResult(name=name, commit_sha=commit, passed=name not in failed,
                            evidence_id="run-" + name)
                for name in self.names
            ),
        )

    def test_all_required_independent_evidence_allows_merge_proposal_only(self):
        decision = self.verifier.decide(self.review(), self.report(), expected_commit=self.sha)
        self.assertTrue(decision.allowed_to_propose_merge)
        self.assertTrue(decision.verification_passed)
        self.assertEqual(decision.reasons, ())

    def test_same_reviewer_and_author_is_rejected(self):
        with self.assertRaises(EvolutionReviewError):
            self.verifier.validate_review(
                self.review(reviewer_id="change-author"), expected_commit=self.sha
            )

    def test_stale_review_commit_is_rejected(self):
        with self.assertRaises(EvolutionReviewError):
            self.verifier.validate_review(
                self.review(commit_sha="b" * 40), expected_commit=self.sha
            )

    def test_approval_with_findings_is_rejected(self):
        with self.assertRaises(EvolutionReviewError):
            self.verifier.validate_review(
                self.review(findings=("unresolved security issue",)), expected_commit=self.sha
            )

    def test_request_changes_requires_findings(self):
        with self.assertRaises(EvolutionReviewError):
            self.verifier.validate_review(
                self.review(verdict=ReviewVerdict.REQUEST_CHANGES), expected_commit=self.sha
            )

    def test_failed_required_check_blocks_gate(self):
        decision = self.verifier.decide(
            self.review(), self.report(failed={"security_tests"}), expected_commit=self.sha
        )
        self.assertFalse(decision.allowed_to_propose_merge)
        self.assertIn("required_verification_not_passed", decision.reasons)

    def test_stale_check_blocks_as_invalid_evidence(self):
        report = self.report(sha="b" * 40)
        with self.assertRaises(EvolutionReviewError):
            self.verifier.validate_verification(report, expected_commit=self.sha)

    def test_duplicate_or_missing_check_is_rejected(self):
        report = self.report()
        duplicate = VerificationReport(
            commit_sha=self.sha,
            checks=report.checks[:-1] + (report.checks[0],),
        )
        with self.assertRaises(EvolutionReviewError):
            self.verifier.validate_verification(duplicate, expected_commit=self.sha)

    def test_unknown_check_is_rejected(self):
        report = VerificationReport(
            commit_sha=self.sha,
            checks=tuple(
                CheckResult(name=("made_up" if i == 0 else name), commit_sha=self.sha,
                            passed=True, evidence_id="run-" + str(i))
                for i, name in enumerate(self.names)
            ),
        )
        with self.assertRaises(EvolutionReviewError):
            self.verifier.validate_verification(report, expected_commit=self.sha)

    def test_abstention_blocks_gate(self):
        decision = self.verifier.decide(
            self.review(verdict=ReviewVerdict.ABSTAIN), self.report(), expected_commit=self.sha
        )
        self.assertFalse(decision.allowed_to_propose_merge)
        self.assertIn("independent_review_not_approved", decision.reasons)


if __name__ == "__main__":
    unittest.main()
