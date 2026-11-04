import tempfile
import unittest
from pathlib import Path

from evaluation.agent_evaluator import AgentEvaluator
from evaluation.agent_review_queue import AgentEvaluationReviewQueue


class AgentEvaluationReviewQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.allowed = {"owner"}
        self.queue = AgentEvaluationReviewQueue(
            str(Path(self.tmp.name) / "reviews.sqlite3"),
            authorize=lambda actor, action, _agent: (
                actor in self.allowed
                and action in {
                    "agent_evaluation:enqueue",
                    "agent_evaluation:read",
                    "agent_evaluation:review",
                }
            ),
        )
        self.report = AgentEvaluator().evaluate(
            task_id="task-001",
            agent_id="coding",
            outcome="succeeded",
            signals={
                "correctness": 0.9,
                "evidence_quality": 0.8,
                "instruction_compliance": 1.0,
                "completeness": 0.9,
                "efficiency": 0.8,
            },
            evidence_refs=["test-run:123", "artifact:456"],
        )

    def test_enqueue_and_list_pending_owner_scoped_review(self):
        queued = self.queue.enqueue(actor_id="owner", report=self.report)
        self.assertEqual(queued["status"], "pending")
        self.assertFalse(queued["memory_written"])
        self.assertFalse(queued["routing_changed"])
        pending = self.queue.list_pending(actor_id="owner")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["report"]["task_id"], "task-001")

    def test_enqueue_is_idempotent(self):
        first = self.queue.enqueue(actor_id="owner", report=self.report)
        second = self.queue.enqueue(actor_id="owner", report=self.report)
        self.assertEqual(first["evaluation_id"], second["evaluation_id"])
        self.assertEqual(len(self.queue.list_pending(actor_id="owner")), 1)

    def test_unauthorized_actor_cannot_enqueue_or_read(self):
        with self.assertRaises(PermissionError):
            self.queue.enqueue(actor_id="other", report=self.report)
        with self.assertRaises(PermissionError):
            self.queue.list_pending(actor_id="other")

    def test_review_decision_is_single_use_and_has_no_side_effects(self):
        self.queue.enqueue(actor_id="owner", report=self.report)
        result = self.queue.decide(
            reviewer_actor_id="owner",
            evaluation_id=self.report.evaluation_id,
            decision="approved",
            note="Evidence reviewed.",
        )
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["review_note"], "Evidence reviewed.")
        self.assertFalse(result["memory_written"])
        self.assertFalse(result["routing_changed"])
        with self.assertRaises(ValueError):
            self.queue.decide(
                reviewer_actor_id="owner",
                evaluation_id=self.report.evaluation_id,
                decision="rejected",
            )

    def test_reject_invalid_decision_and_report(self):
        with self.assertRaises(ValueError):
            self.queue.decide(
                reviewer_actor_id="owner",
                evaluation_id="eval-task-001",
                decision="maybe",
            )
        poor_report = AgentEvaluator().evaluate(
            task_id="task-002",
            agent_id="coding",
            outcome="failed",
            signals={
                "correctness": 0.0,
                "evidence_quality": 0.0,
                "instruction_compliance": 0.5,
                "completeness": 0.0,
                "efficiency": 0.5,
            },
            evidence_refs=[],
        )
        self.assertTrue(poor_report.requires_human_review)
        self.assertEqual(
            self.queue.enqueue(actor_id="owner", report=poor_report)["status"],
            "pending",
        )

    def test_non_reviewable_report_is_rejected(self):
        report = AgentEvaluator().evaluate(
            task_id="task-003",
            agent_id="coding",
            outcome="succeeded",
            signals={
                "correctness": 1.0,
                "evidence_quality": 1.0,
                "instruction_compliance": 1.0,
                "completeness": 1.0,
                "efficiency": 1.0,
            },
            evidence_refs=["test-run:789", "artifact:987"],
        )
        self.assertFalse(report.requires_human_review)
        self.assertFalse(report.eligible_for_memory_review)
        with self.assertRaises(ValueError):
            self.queue.enqueue(actor_id="owner", report=report)


if __name__ == "__main__":
    unittest.main()
