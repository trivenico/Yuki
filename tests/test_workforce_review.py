import unittest

from core.workforce_review import IndependentQualityReviewer, REQUIRED_CHECKS


class IndependentQualityReviewerTests(unittest.TestCase):
    def setUp(self):
        self.good_report = {
            "checks": {name: True for name in REQUIRED_CHECKS},
            "findings": [],
        }

    def test_approves_only_when_every_required_check_passes(self):
        reviewer = IndependentQualityReviewer(
            reviewer_id="QA-001",
            evaluator=lambda **kwargs: self.good_report,
        )
        result = reviewer.review(
            description="Implement a safe API endpoint",
            employee_id="ENG-001",
            execution_status="completed",
            execution_message="Implementation summary",
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.status, "approved")
        self.assertEqual(result.reviewer_id, "QA-001")

    def test_failed_check_blocks_approval(self):
        report = {
            "checks": {name: True for name in REQUIRED_CHECKS},
            "findings": [],
        }
        report["checks"]["security"] = False
        reviewer = IndependentQualityReviewer(
            reviewer_id="QA-001",
            evaluator=lambda **kwargs: report,
        )
        result = reviewer.review(
            description="Review endpoint",
            employee_id="ENG-001",
            execution_status="completed",
            execution_message="",
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.status, "quality_gate_failed")

    def test_findings_block_approval(self):
        report = {
            "checks": {name: True for name in REQUIRED_CHECKS},
            "findings": ["No regression test evidence"],
        }
        reviewer = IndependentQualityReviewer(
            reviewer_id="QA-001",
            evaluator=lambda **kwargs: report,
        )
        result = reviewer.review(
            description="Review endpoint",
            employee_id="ENG-001",
            execution_status="completed",
            execution_message="",
        )
        self.assertFalse(result.approved)

    def test_malformed_report_fails_closed(self):
        reviewer = IndependentQualityReviewer(
            reviewer_id="QA-001",
            evaluator=lambda **kwargs: {"checks": {"security": "yes"}, "findings": []},
        )
        result = reviewer.review(
            description="Review endpoint",
            employee_id="ENG-001",
            execution_status="completed",
            execution_message="",
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.status, "invalid_review_report")

    def test_self_review_is_denied(self):
        reviewer = IndependentQualityReviewer(
            reviewer_id="ENG-001",
            evaluator=lambda **kwargs: self.good_report,
        )
        result = reviewer.review(
            description="Review endpoint",
            employee_id="ENG-001",
            execution_status="completed",
            execution_message="",
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.status, "self_review_denied")


if __name__ == "__main__":
    unittest.main()
