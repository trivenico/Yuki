"""Evidence-based, deterministic evaluation of completed specialist tasks.

This evaluator scores supplied, independently checked signals. It does not ask a
model to grade itself, execute tools, alter routing, or approve memory writes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from typing import Mapping

_AGENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_TASK = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.:@/-]{0,199}$")
_DIMENSIONS = {
    "correctness": 0.35,
    "evidence_quality": 0.25,
    "instruction_compliance": 0.20,
    "completeness": 0.15,
    "efficiency": 0.05,
}


@dataclass(frozen=True)
class EvaluationReport:
    evaluation_id: str
    task_id: str
    agent_id: str
    score: float
    dimensions: dict[str, float]
    confidence: str
    evidence_refs: tuple[str, ...]
    requires_human_review: bool
    eligible_for_memory_review: bool
    automatic_memory_write: bool
    routing_changed: bool
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


class AgentEvaluator:
    """Score independently verified task signals using a fixed, versioned rubric."""

    rubric_version = "1.0"

    def __init__(self, *, human_review_below: float = 0.85,
                 minimum_evidence_refs: int = 1):
        if isinstance(human_review_below, bool) or not isinstance(human_review_below, (int, float)) or not 0 <= human_review_below <= 1:
            raise ValueError("human_review_below must be between 0 and 1")
        if type(minimum_evidence_refs) is not int or not 1 <= minimum_evidence_refs <= 20:
            raise ValueError("minimum_evidence_refs must be between 1 and 20")
        self.human_review_below = float(human_review_below)
        self.minimum_evidence_refs = minimum_evidence_refs

    def evaluate(self, *, task_id: str, agent_id: str, outcome: str,
                 signals: Mapping[str, bool | float],
                 evidence_refs: tuple[str, ...] | list[str]) -> EvaluationReport:
        if not isinstance(task_id, str) or not _TASK.fullmatch(task_id):
            raise ValueError("invalid_task_id")
        if not isinstance(agent_id, str) or not _AGENT.fullmatch(agent_id):
            raise ValueError("invalid_agent_id")
        if not isinstance(outcome, str) or outcome not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("invalid_outcome")
        if not isinstance(signals, Mapping) or set(signals) != set(_DIMENSIONS):
            raise ValueError("signals must contain exactly the five rubric dimensions")
        normalized = {}
        for name, value in signals.items():
            if isinstance(value, bool):
                normalized[name] = 1.0 if value else 0.0
            elif isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
                normalized[name] = float(value)
            else:
                raise ValueError(f"invalid_signal:{name}")
        if not isinstance(evidence_refs, (tuple, list)) or len(evidence_refs) > 20:
            raise ValueError("evidence_refs must be a list or tuple of at most 20 items")
        refs = []
        for ref in evidence_refs:
            if not isinstance(ref, str) or not ref.strip() or len(ref) > 500:
                raise ValueError("evidence references must be bounded non-empty strings")
            refs.append(ref.strip())
        if len(set(refs)) != len(refs):
            raise ValueError("evidence references must be unique")
        evidence_sufficient = len(refs) >= self.minimum_evidence_refs
        score = sum(normalized[key] * weight for key, weight in _DIMENSIONS.items())
        if outcome != "succeeded":
            score = 0.0
        if not evidence_sufficient:
            score = min(score, 0.49)
        score = round(score, 4)
        missing_dimensions = sum(value < 0.5 for value in normalized.values())
        confidence = "high" if evidence_sufficient and len(refs) >= 2 and missing_dimensions == 0 else (
            "medium" if evidence_sufficient else "low"
        )
        review = outcome != "succeeded" or score < self.human_review_below or not evidence_sufficient
        return EvaluationReport(
            evaluation_id=f"eval-{task_id}",
            task_id=task_id,
            agent_id=agent_id,
            score=score,
            dimensions=normalized,
            confidence=confidence,
            evidence_refs=tuple(refs),
            requires_human_review=review,
            eligible_for_memory_review=outcome == "succeeded" and score >= self.human_review_below and evidence_sufficient,
            automatic_memory_write=False,
            routing_changed=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def rubric(self) -> dict:
        return {
            "version": self.rubric_version,
            "weights": dict(_DIMENSIONS),
            "human_review_below": self.human_review_below,
            "automatic_memory_write": False,
            "automatic_routing_changes": False,
        }
