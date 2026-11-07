"""Tests for evidence-gated reviewer remediation planning and approval."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation.reviewer_incident_intelligence import (
    IncidentFinding, IncidentIntelligenceReport, RemediationRecommendation,
)
from automation.reviewer_remediation_planner import (
    ReviewerRemediationPlanner, ReviewerRemediationPlanStore,
)


class ReviewerRemediationPlannerTests(unittest.TestCase):
    def setUp(self):
        self.report = IncidentIntelligenceReport(
            generated_at="2026-11-07T12:00:00+00:00",
            incidents_analyzed=3,
            findings=(IncidentFinding(
                code="replacement_capacity", severity="high", count=3,
                affected_tasks=("task-a",), affected_workers=("worker-a",),
                summary="Repeated replacement capacity failure.",
                recommendation_codes=("review_reviewer_capacity",),
                related_commits=(),
            ),),
            recommendations=(RemediationRecommendation(
                "review_reviewer_capacity", "high",
                "Review reviewer pool capacity.",
            ),),
            evidence_limitations=("Only supplied evidence was analyzed.",),
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ReviewerRemediationPlanStore(
            Path(self.tmp.name) / "plans.sqlite",
            authorize=lambda actor, action, plan: actor == "operator",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_planner_creates_pending_reversible_plan_with_evidence(self):
        plans = ReviewerRemediationPlanner().build(self.report)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].approval_state, "pending")
        self.assertTrue(plans[0].reversible)
        self.assertIn("reviewer_pool_snapshot", plans[0].required_evidence)
        self.store.save(actor_id="operator", plans=plans)

    def test_approval_and_rejection_are_authorized_and_terminal(self):
        plan = ReviewerRemediationPlanner().build(self.report)[0]
        self.store.save(actor_id="operator", plans=(plan,))
        with self.assertRaises(PermissionError):
            self.store.decide(
                actor_id="intruder", plan_id=plan.plan_id,
                decision="approved", reason_code="looks_safe",
            )
        decision = self.store.decide(
            actor_id="operator", plan_id=plan.plan_id,
            decision="approved", reason_code="evidence_reviewed",
        )
        self.assertEqual(decision.state, "approved")
        with self.assertRaises(ValueError):
            self.store.decide(
                actor_id="operator", plan_id=plan.plan_id,
                decision="rejected", reason_code="changed_mind",
            )

    def test_execution_receipt_requires_approval_and_digest(self):
        plan = ReviewerRemediationPlanner().build(self.report)[0]
        self.store.save(actor_id="operator", plans=(plan,))
        with self.assertRaises(ValueError):
            self.store.record_execution_receipt(
                actor_id="operator", plan_id=plan.plan_id,
                outcome="succeeded", evidence_digest="a" * 64,
            )
        self.store.decide(
            actor_id="operator", plan_id=plan.plan_id,
            decision="approved", reason_code="evidence_reviewed",
        )
        self.store.record_execution_receipt(
            actor_id="operator", plan_id=plan.plan_id,
            outcome="succeeded", evidence_digest="a" * 64,
        )
        with self.assertRaises(ValueError):
            self.store.record_execution_receipt(
                actor_id="operator", plan_id=plan.plan_id,
                outcome="succeeded", evidence_digest="a" * 64,
            )

    def test_rejection_does_not_allow_execution_receipt(self):
        plan = ReviewerRemediationPlanner().build(self.report)[0]
        self.store.save(actor_id="operator", plans=(plan,))
        self.store.decide(
            actor_id="operator", plan_id=plan.plan_id,
            decision="rejected", reason_code="insufficient_evidence",
        )
        with self.assertRaises(ValueError):
            self.store.record_execution_receipt(
                actor_id="operator", plan_id=plan.plan_id,
                outcome="succeeded", evidence_digest="b" * 64,
            )


if __name__ == "__main__":
    unittest.main()
