import unittest
from datetime import datetime, timezone

from automation.independent_verification import (
    VerificationCheck, VerificationReport, evaluate_verification, sha256_artifact,
)

REQUIRED = (
    "acceptance_criteria", "tests", "security", "architecture", "claims_and_provenance"
)


def report(statuses=None, author="builder", reviewers=None):
    statuses = statuses or {name: "passed" for name in REQUIRED}
    reviewers = reviewers or {}
    checks = tuple(VerificationCheck(
        name=name,
        reviewer_agent_id=reviewers.get(name, "reviewer-" + name),
        status=status,
        evidence_reference="artifact://evidence/" + name,
        summary="Evidence submitted for " + name,
    ) for name, status in statuses.items())
    return VerificationReport(
        artifact_sha256=sha256_artifact(b"exact artifact bytes"),
        author_agent_id=author,
        checks=checks,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


class IndependentVerificationTests(unittest.TestCase):
    def test_all_independent_passes_accept(self):
        decision = evaluate_verification(report())
        self.assertTrue(decision.accepted)
        self.assertEqual(set(decision.passed_checks), set(REQUIRED))

    def test_missing_check_fails_closed(self):
        statuses = {name: "passed" for name in REQUIRED if name != "security"}
        decision = evaluate_verification(report(statuses))
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.missing_checks, ("security",))

    def test_failed_blocked_and_not_run_checks_reject(self):
        for status in ("failed", "blocked", "not_run"):
            with self.subTest(status=status):
                statuses = {name: "passed" for name in REQUIRED}
                statuses["tests"] = status
                decision = evaluate_verification(report(statuses))
                self.assertFalse(decision.accepted)

    def test_author_cannot_review_own_artifact(self):
        reviewers = {name: "builder" if name == "security" else "reviewer-" + name for name in REQUIRED}
        with self.assertRaisesRegex(ValueError, "author_cannot_verify"):
            evaluate_verification(report(reviewers=reviewers))

    def test_duplicate_checks_rejected(self):
        valid = report()
        duplicated = VerificationReport(
            valid.artifact_sha256, valid.author_agent_id,
            valid.checks + (valid.checks[0],), valid.created_at,
        )
        with self.assertRaisesRegex(ValueError, "duplicate_verification"):
            evaluate_verification(duplicated)

    def test_naive_timestamp_rejected(self):
        valid = report()
        invalid = VerificationReport(
            valid.artifact_sha256, valid.author_agent_id, valid.checks,
            "2026-01-01T00:00:00",
        )
        with self.assertRaisesRegex(ValueError, "timezone_aware"):
            evaluate_verification(invalid)

    def test_evidence_is_bound_to_exact_artifact_bytes(self):
        self.assertNotEqual(sha256_artifact(b"version A"), sha256_artifact(b"version B"))


if __name__ == "__main__":
    unittest.main()
