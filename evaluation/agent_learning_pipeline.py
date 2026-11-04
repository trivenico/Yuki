"""Orchestrate deterministic evaluation and owner-scoped human review.

The caller supplies independently checked signals and evidence references. This
module does not infer correctness from model output, execute tools, or write
long-term memory.
"""
from __future__ import annotations

from evaluation.agent_evaluator import AgentEvaluator
from evaluation.agent_review_queue import AgentEvaluationReviewQueue


class AgentLearningPipeline:
    """Connect completed-task evaluation to the durable review queue."""

    def __init__(self, evaluator: AgentEvaluator, review_queue: AgentEvaluationReviewQueue):
        if not isinstance(evaluator, AgentEvaluator):
            raise TypeError("evaluator_must_be_AgentEvaluator")
        if not isinstance(review_queue, AgentEvaluationReviewQueue):
            raise TypeError("review_queue_must_be_AgentEvaluationReviewQueue")
        self.evaluator = evaluator
        self.review_queue = review_queue

    def evaluate_completed_task(
        self, *, actor_id: str, task_id: str, agent_id: str, outcome: str,
        independently_verified_signals: dict, evidence_refs: list[str] | tuple[str, ...],
    ) -> dict:
        """Evaluate a completed task and persist its human-review disposition.

        Evidence verification is intentionally the responsibility of the caller:
        references must point to actual test results, artifacts, or other trusted
        observations, not merely the agent's self-reported claims.
        """
        if not isinstance(independently_verified_signals, dict):
            raise TypeError("independently_verified_signals_must_be_dict")
        report = self.evaluator.evaluate(
            task_id=task_id,
            agent_id=agent_id,
            outcome=outcome,
            signals=independently_verified_signals,
            evidence_refs=evidence_refs,
        )
        queued = self.review_queue.enqueue(actor_id=actor_id, report=report)
        return {
            "evaluation": report.to_dict(),
            "review": queued,
            "learning_state": "awaiting_human_review",
            "memory_written": False,
            "routing_changed": False,
            "model_updated": False,
        }
