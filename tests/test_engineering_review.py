import json
import unittest
from core.engineering_review import (
    CheckEvidence, EngineeringReviewReport, ReviewFinding, ReviewReportError,
    evaluate_review_gate,
)

class EngineeringReviewTests(unittest.TestCase):
    def report(self, **kwargs):
        base = dict(
            task_id="task-123",
            checks=(CheckEvidence("unit", "passed", command="python -m unittest", summary="ok"),),
            changed_files=("core/example.py",),
            recommendation="ready-for-required-checks",
        )
        base.update(kwargs)
        return EngineeringReviewReport(**base)

    def test_round_trip_json(self):
        original = self.report(findings=(ReviewFinding("medium", "core/example.py",
            "Malformed input triggers exception", "Request fails", "Validate input first", 12),))
        restored = EngineeringReviewReport.from_json(original.to_json())
        self.assertEqual(restored, original)

    def test_high_severity_finding_blocks(self):
        report = self.report(findings=(ReviewFinding("high", "core/auth.py",
            "Untrusted request bypasses authorization", "Privileged action runs",
            "Enforce authorization at the execution boundary"),))
        self.assertEqual(evaluate_review_gate(report, required_checks={"unit"}), "block")

    def test_missing_or_skipped_required_check_blocks(self):
        report = self.report(checks=(CheckEvidence("unit", "passed"), CheckEvidence("security", "skipped")))
        self.assertEqual(evaluate_review_gate(report, required_checks={"unit", "security"}), "block")
        report2 = self.report()
        self.assertEqual(evaluate_review_gate(report2, required_checks={"unit", "security"}), "block")

    def test_human_approval_is_not_inferred_from_model_recommendation(self):
        report = self.report()
        self.assertEqual(evaluate_review_gate(report, required_checks={"unit"},
            require_human_approval=True, human_approved=False), "needs-human-review")

    def test_unknown_json_fields_rejected(self):
        raw = json.dumps({"task_id":"t", "recommendation":"ready-for-required-checks", "untrusted":"x"})
        with self.assertRaises(ReviewReportError):
            EngineeringReviewReport.from_json(raw)

    def test_invalid_severity_rejected(self):
        report = self.report(findings=(ReviewFinding("urgent", "x.py", "scenario", "impact", "fix"),))
        with self.assertRaises(ReviewReportError):
            report.validate()

    def test_failed_check_blocks_even_if_report_says_ready(self):
        report = self.report(checks=(CheckEvidence("unit", "failed"),))
        self.assertEqual(evaluate_review_gate(report, required_checks={"unit"}), "block")

if __name__ == "__main__":
    unittest.main()
