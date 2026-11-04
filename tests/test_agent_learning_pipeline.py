import tempfile
import unittest
from pathlib import Path

from evaluation.agent_evaluator import AgentEvaluator
from evaluation.agent_review_queue import AgentEvaluationReviewQueue
from evaluation.agent_learning_pipeline import AgentLearningPipeline


class AgentLearningPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = AgentEvaluationReviewQueue(
            str(Path(self.temp.name) / "learning.sqlite3"),
            authorize=lambda actor, action, _agent: actor == "owner" and action in {
                "agent_evaluation:enqueue", "agent_evaluation:read",
                "agent_evaluation:review",
            },
        )
        self.pipeline = AgentLearningPipeline(AgentEvaluator(), self.queue)
        self.signals = {
            "correctness": 1.0,
            "evidence_quality": 1.0,
            "instruction_compliance": 1.0,
            "completeness": 1.0,
            "efficiency": 1.0,
        }

    def test_successful_task_is_queued_for_human_knowledge_review(self):
        result = self.pipeline.evaluate_completed_task(
            actor_id="owner", task_id="task-101", agent_id="research",
            outcome="succeeded", independently_verified_signals=self.signals,
            evidence_refs=["test-run:101", "artifact:101"],
        )
        self.assertTrue(result["evaluation"]["eligible_for_memory_review"])
        self.assertEqual(result["review"]["status"], "pending")
        self.assertEqual(result["learning_state"], "awaiting_human_review")
        self.assertFalse(result["memory_written"])
        self.assertFalse(result["routing_changed"])
        self.assertFalse(result["model_updated"])

    def test_failed_or_weak_evidence_task_requires_review_and_is_not_promoted(self):
        weak = dict(self.signals, correctness=0.1)
        result = self.pipeline.evaluate_completed_task(
            actor_id="owner", task_id="task-102", agent_id="research",
            outcome="failed", independently_verified_signals=weak,
            evidence_refs=[],
        )
        self.assertTrue(result["evaluation"]["requires_human_review"])
        self.assertFalse(result["evaluation"]["eligible_for_memory_review"])
        self.assertEqual(result["review"]["status"], "pending")
        self.assertFalse(result["memory_written"])

    def test_unauthorized_actor_cannot_submit_evaluation(self):
        with self.assertRaises(PermissionError):
            self.pipeline.evaluate_completed_task(
                actor_id="intruder", task_id="task-103", agent_id="research",
                outcome="succeeded", independently_verified_signals=self.signals,
                evidence_refs=["test-run:103"],
            )

    def test_signal_input_must_be_explicit_mapping(self):
        with self.assertRaisesRegex(TypeError, "must_be_dict"):
            self.pipeline.evaluate_completed_task(
                actor_id="owner", task_id="task-104", agent_id="research",
                outcome="succeeded", independently_verified_signals=None,
                evidence_refs=["test-run:104"],
            )


if __name__ == "__main__":
    unittest.main()
