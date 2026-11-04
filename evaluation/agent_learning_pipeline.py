"""Orchestrate deterministic evaluation and owner-scoped human review."""
from __future__ import annotations
from evaluation.agent_evaluator import AgentEvaluator
from evaluation.agent_review_queue import AgentEvaluationReviewQueue


class AgentLearningPipeline:
    """Evaluate trusted task signals and queue reports without writing memory."""

    def __init__(self, evaluator: AgentEvaluator, review_queue: AgentEvaluationReviewQueue):
        if not isinstance(evaluator, AgentEvaluator):
            raise TypeError("evaluator_must_be_AgentEvaluator")
        if not isinstance(review_queue, AgentEvaluationReviewQueue):
            raise TypeError("review_queue_must_be_AgentEvaluationReviewQueue")
        self.evaluator, self.review_queue = evaluator, review_queue

    def evaluate_completed_task(self, *, actor_id: str, task_id: str, agent_id: str,
                                outcome: str, independently_verified_signals: dict,
                                evidence_refs: list[str] | tuple[str, ...]) -> dict:
        if not isinstance(independently_verified_signals, dict):
            raise TypeError("independently_verified_signals_must_be_dict")
        report = self.evaluator.evaluate(
            task_id=task_id, agent_id=agent_id, outcome=outcome,
            signals=independently_verified_signals, evidence_refs=evidence_refs,
        )
        queued = self.review_queue.enqueue(actor_id=actor_id, report=report)
        return {
            "evaluation": report.to_dict(), "review": queued,
            "learning_state": "awaiting_human_review",
            "memory_written": False, "routing_changed": False, "model_updated": False,
        }
