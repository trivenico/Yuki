import unittest

from automation.reviewer_contracts import (
    ReviewContractError, ReviewFinding, ReviewTask, build_review_result,
)


def task():
    return ReviewTask(
        task_id="review-security-001",
        artifact_sha256="a" * 64,
        author_agent_id="builder",
        reviewer_agent_id="security-reviewer",
        check_name="security",
        requirements=("Inspect input validation", "Check authorization boundaries"),
        evidence_references=("snapshot://artifact/a",),
    )


class ReviewerContractTests(unittest.TestCase):
    def test_builds_result_bound_to_task(self):
        result = build_review_result(
            task(), status="passed", summary="No blocking security issues found.",
            findings=(ReviewFinding(
                finding_id="finding-1", severity="low", state="resolved",
                title="Missing explanatory comment", detail="The authorization branch is not documented.",
                evidence_reference="snapshot://artifact/a", recommendation="Add a short comment.",
            ),),
        )
        self.assertEqual(result.artifact_sha256, "a" * 64)
        self.assertEqual(result.reviewer_agent_id, "security-reviewer")

    def test_rejects_author_self_review(self):
        invalid = ReviewTask(
            task_id="review-1", artifact_sha256="a" * 64,
            author_agent_id="same-agent", reviewer_agent_id="same-agent",
            check_name="security", requirements=("Inspect security",), evidence_references=(),
        )
        with self.assertRaisesRegex(ReviewContractError, "reviewer_must_differ"):
            invalid.validate()

    def test_rejects_result_rebound_to_another_artifact(self):
        result = build_review_result(task(), status="failed", summary="Issue found.")
        altered = ReviewTask(
            task_id="review-security-001", artifact_sha256="b" * 64,
            author_agent_id="builder", reviewer_agent_id="security-reviewer",
            check_name="security", requirements=("Inspect input validation", "Check authorization boundaries"),
            evidence_references=("snapshot://artifact/b",),
        )
        with self.assertRaisesRegex(ReviewContractError, "binding_mismatch"):
            result.validate(altered)

    def test_passed_result_cannot_hide_open_high_or_critical_findings(self):
        finding = ReviewFinding(
            finding_id="finding-critical", severity="critical", state="open",
            title="Authentication bypass", detail="An unauthenticated caller can reach a privileged operation.",
            evidence_reference="snapshot://artifact/a",
        )
        with self.assertRaisesRegex(ReviewContractError, "open_blockers"):
            build_review_result(task(), status="passed", summary="Looks good.", findings=(finding,))

    def test_passed_result_cannot_hide_open_medium_findings(self):
        finding = ReviewFinding(
            finding_id="finding-medium", severity="medium", state="open",
            title="Insufficient audit detail", detail="A security-sensitive change is not fully audited.",
            evidence_reference="snapshot://artifact/a",
        )
        with self.assertRaisesRegex(ReviewContractError, "open_medium"):
            build_review_result(task(), status="passed", summary="Looks good.", findings=(finding,))

    def test_rejects_duplicate_finding_ids(self):
        finding = ReviewFinding(
            finding_id="duplicate", severity="low", state="resolved",
            title="Note", detail="A finding.", evidence_reference="snapshot://artifact/a",
        )
        with self.assertRaisesRegex(ReviewContractError, "duplicate_finding"):
            build_review_result(task(), status="failed", summary="Review has findings.", findings=(finding, finding))

    def test_rejects_unsupported_check(self):
        invalid = ReviewTask(
            task_id="review-1", artifact_sha256="a" * 64,
            author_agent_id="builder", reviewer_agent_id="reviewer",
            check_name="deployment", requirements=("Inspect deployment",), evidence_references=(),
        )
        with self.assertRaisesRegex(ReviewContractError, "unsupported_review_check"):
            invalid.validate()


if __name__ == "__main__":
    unittest.main()
