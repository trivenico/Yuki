"""Tests for evidence-grounded reviewer incident intelligence."""
from __future__ import annotations

import unittest

from automation.reviewer_incident_intelligence import (
    DeploymentEvidence, IncidentEvidence, ReviewerIncidentIntelligence,
)


class ReviewerIncidentIntelligenceTests(unittest.TestCase):
    def test_repeated_failures_are_grouped_and_deployment_correlated(self):
        rows = tuple(IncidentEvidence(
            task_id=f"task-{i}", worker_id="reviewer-a", state="cooldown",
            attempts=2, max_attempts=5, last_reason_code="no_eligible_replacement",
            updated_at=f"2026-11-07T12:0{i}:00+00:00",
        ) for i in range(3))
        releases = (DeploymentEvidence(
            commit_sha="abc123", deployed_at="2026-11-07T12:00:00+00:00",
            environment="production",
        ),)
        report = ReviewerIncidentIntelligence(
            repeated_failure_threshold=3,
        ).analyze(rows, deployments=releases)
        finding = next(x for x in report.findings if x.code == "replacement_capacity")
        self.assertEqual(finding.count, 3)
        self.assertEqual(finding.severity, "high")
        self.assertEqual(finding.related_commits, ("abc123",))
        self.assertIn("review_reviewer_capacity",
                      [x.code for x in report.recommendations])

    def test_dead_letter_finding_requires_human_review(self):
        row = IncidentEvidence(
            task_id="task-dead", worker_id="reviewer-z", state="dead_letter",
            attempts=5, max_attempts=5, last_reason_code="replacement_resolver_failed",
            updated_at="2026-11-07T12:00:00+00:00",
        )
        report = ReviewerIncidentIntelligence().analyze((row,))
        finding = next(x for x in report.findings if x.code == "dead_letter_backlog")
        self.assertEqual(finding.severity, "critical")
        rec = next(x for x in report.recommendations if x.code == "review_dead_letter_backlog")
        self.assertTrue(rec.requires_human_approval)

    def test_temporal_correlation_is_explicitly_not_causation(self):
        report = ReviewerIncidentIntelligence().analyze((), deployments=(
            DeploymentEvidence("sha", "2026-11-07T12:00:00+00:00", "production"),
        ))
        self.assertTrue(any("not proof of causation" in x
                            for x in report.evidence_limitations))

    def test_invalid_or_naive_timestamps_fail_closed(self):
        row = IncidentEvidence(
            "task", "worker", "open", 0, 5, "unknown",
            "2026-11-07T12:00:00",
        )
        with self.assertRaises(ValueError):
            ReviewerIncidentIntelligence().analyze((row,))

    def test_input_bounds_are_enforced(self):
        with self.assertRaises(ValueError):
            ReviewerIncidentIntelligence().analyze(
                (IncidentEvidence("t","w","open",0,5,"unknown",
                                  "2026-11-07T12:00:00+00:00"),) * 5001
            )


if __name__ == "__main__":
    unittest.main()
