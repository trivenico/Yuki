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
                "agent_evaluation:enqueue", "agent_evaluation:read", "agent_evaluation:review"
            },
        )
        self.pipeline = AgentLearningPipeline(AgentEvaluator(), self.queue)
        self.signals = {k: 1.0 for k in (
            "correctness", "evidence_quality", "instruction_compliance", "completeness", "efficiency"
        )}

    def test_successful_task_is_queued_without_memory_side_effects(self):
        result = self.pipeline.evaluate_completed_task(
            actor_id="owner", task_id="task-101", agent_id="research", outcome="succeeded",
            independently_verified_signals=self.signals,
            evidence_refs=["test-run:101", "artifact:101"],
        )
        self.assertTrue(result["evaluation"]["eligible_for_memory_review"])
        self.assertEqual(result["review"]["status"], "pending")
        self.assertFalse(result["memory_written"])
        self.assertFalse(result["routing_changed"])
        self.assertFalse(result["model_updated"])

    def test_failed_task_is_queued_for_review_but_not_memory_eligible(self):
        result = self.pipeline.evaluate_completed_task(
            actor_id="owner", task_id="task-102", agent_id="research", outcome="failed",
            independently_verified_signals=self.signals, evidence_refs=[],
        )
        self.assertTrue(result["evaluation"]["requires_human_review"])
        self.assertFalse(result["evaluation"]["eligible_for_memory_review"])
        self.assertEqual(result["review"]["status"], "pending")

    def test_unauthorized_actor_is_rejected(self):
        with self.assertRaises(PermissionError):
            self.pipeline.evaluate_completed_task(
                actor_id="intruder", task_id="task-103", agent_id="research", outcome="succeeded",
                independently_verified_signals=self.signals, evidence_refs=["test:103"],
            )

    def test_signal_input_must_be_dict(self):
        with self.assertRaisesRegex(TypeError, "must_be_dict"):
            self.pipeline.evaluate_completed_task(
                actor_id="owner", task_id="task-104", agent_id="research", outcome="succeeded",
                independently_verified_signals=None, evidence_refs=["test:104"],
            )


if __name__ == "__main__":
    unittest.main()
