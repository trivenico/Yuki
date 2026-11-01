import json
import unittest

from automation.independent_verification import (
    IndependentVerificationEngine, VerificationError,
)


class FakeReviewer:
    def __init__(self, response):
        self.response = response
        self.prompts = []
    def is_available(self):
        return True
    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.response


def response(status="approved", findings=None):
    return json.dumps({
        "status": status,
        "summary": "Assessment based on supplied evidence only.",
        "findings": findings or [],
    })


class IndependentVerificationTests(unittest.TestCase):
    def setUp(self):
        self.reviewer = FakeReviewer(response())
        self.engine = IndependentVerificationEngine(
            self.reviewer, reviewer_id="reviewer-model", author_id="builder-model"
        )

    def test_independent_review_is_advisory_and_digest_bound(self):
        report = self.engine.review(
            requirements=["Add bounded input validation."],
            artifact={"files": ["module.py"], "change_summary": "Validate inputs."},
            evidence=[{"kind": "test", "description": "unit suite", "result": "passed"}],
            tests_passed=True,
        )
        self.assertEqual(report.status, "approved")
        self.assertTrue(report.human_approval_required)
        self.assertTrue(report.advisory_only)
        self.assertEqual(len(report.artifact_sha256), 64)
        self.assertIn("UNTRUSTED REVIEW INPUT", self.reviewer.prompts[0])

    def test_same_reviewer_and_author_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            IndependentVerificationEngine(
                self.reviewer, reviewer_id="same", author_id="SAME"
            )

    def test_failed_tests_cannot_be_overridden_by_reviewer(self):
        report = self.engine.review(
            requirements=["Implement feature."], artifact={"change": "done"},
            evidence=[{"kind": "test", "result": "failed"}], tests_passed=False,
        )
        self.assertEqual(report.status, "changes_requested")
        self.assertIn("tests did not pass", report.summary)

    def test_approval_without_evidence_becomes_inconclusive(self):
        report = self.engine.review(
            requirements=["Implement feature."], artifact={"change": "done"},
            evidence=[], tests_passed=True,
        )
        self.assertEqual(report.status, "inconclusive")

    def test_invalid_json_and_schema_fail_closed(self):
        for raw in ("not json", json.dumps({"status": "approved"})):
            engine = IndependentVerificationEngine(
                FakeReviewer(raw), reviewer_id="reviewer", author_id="builder"
            )
            with self.assertRaises(VerificationError):
                engine.review(
                    requirements=["Requirement"], artifact={"change": "x"},
                    evidence=[{"kind": "test", "result": "passed"}],
                    tests_passed=True,
                )

    def test_rejects_unbounded_or_unknown_evidence_fields(self):
        with self.assertRaises(ValueError):
            self.engine.review(
                requirements=["Requirement"], artifact={"change": "x"},
                evidence=[{"kind": "test", "command": "rm -rf /"}],
                tests_passed=True,
            )
        with self.assertRaises(ValueError):
            self.engine.review(
                requirements=["Requirement"], artifact={"change": "x"},
                evidence=[{"kind": "test", "result": "passed"}] * 81,
                tests_passed=True,
            )

    def test_rejects_non_json_artifacts_and_oversized_inputs(self):
        with self.assertRaises(ValueError):
            self.engine.review(
                requirements=["Requirement"], artifact={"bad": object()},
                evidence=[], tests_passed=True,
            )
        with self.assertRaises(ValueError):
            self.engine.review(
                requirements=["x"] * 41, artifact={"change": "x"},
                evidence=[], tests_passed=True,
            )


if __name__ == "__main__":
    unittest.main()
