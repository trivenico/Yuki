"""Deterministic, offline evaluation for Yuki's continual-memory retrieval.

This module measures retrieval quality; it does not train model weights, mutate
memory, or make authorization decisions. Supply a pure, owner-scoped retriever
over a frozen evaluation snapshot rather than MemoryStore.retrieve(), which
records usage as a side effect.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence


@dataclass(frozen=True)
class LearningEvaluationCase:
    case_id: str
    query: str
    relevant_memory_ids: tuple[str, ...]


@dataclass(frozen=True)
class LearningEvaluationReport:
    case_count: int
    hit_rate_at_k: float
    precision_at_k: float
    recall_at_k: float
    mean_reciprocal_rank: float
    k: int


def evaluate_memory_retrieval(
    cases: Sequence[LearningEvaluationCase],
    retrieve_ids: Callable[[str, int], Sequence[str]],
    *,
    k: int = 5,
) -> LearningEvaluationReport:
    """Evaluate a frozen retrieval implementation against owner-curated labels.

    The callback must be deterministic, side-effect-free, and already scoped to
    the owner whose evaluation set is being measured. Only IDs are accepted so
    report output cannot accidentally disclose stored memory text.
    """
    if not isinstance(cases, (list, tuple)) or not 1 <= len(cases) <= 500:
        raise ValueError("cases must contain between 1 and 500 evaluation cases.")
    if not callable(retrieve_ids):
        raise TypeError("retrieve_ids must be callable.")
    if type(k) is not int or not 1 <= k <= 20:
        raise ValueError("k must be an integer between 1 and 20.")

    seen_case_ids: set[str] = set()
    hit_count = 0
    precision_total = 0.0
    recall_total = 0.0
    reciprocal_rank_total = 0.0

    for case in cases:
        if not isinstance(case, LearningEvaluationCase):
            raise TypeError("each case must be a LearningEvaluationCase.")
        if (
            not isinstance(case.case_id, str)
            or not case.case_id.strip()
            or len(case.case_id) > 128
            or case.case_id in seen_case_ids
        ):
            raise ValueError("case IDs must be unique non-empty text up to 128 characters.")
        seen_case_ids.add(case.case_id)
        if not isinstance(case.query, str) or not case.query.strip() or len(case.query) > 4000:
            raise ValueError("evaluation queries must be non-empty text up to 4000 characters.")
        relevant = case.relevant_memory_ids
        if (
            not isinstance(relevant, tuple)
            or not 1 <= len(relevant) <= 20
            or any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in relevant)
            or len(set(relevant)) != len(relevant)
        ):
            raise ValueError("relevant_memory_ids must be 1-20 unique non-empty IDs.")

        ranked = retrieve_ids(case.query, k)
        if not isinstance(ranked, (list, tuple)) or len(ranked) > k:
            raise ValueError("retriever must return a list or tuple containing at most k IDs.")
        if any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in ranked):
            raise ValueError("retriever returned an invalid memory ID.")
        if len(set(ranked)) != len(ranked):
            raise ValueError("retriever returned duplicate memory IDs.")

        relevant_set = set(relevant)
        found_positions = [index for index, value in enumerate(ranked, 1) if value in relevant_set]
        hits = len(found_positions)
        if hits:
            hit_count += 1
            reciprocal_rank_total += 1.0 / found_positions[0]
        precision_total += hits / k
        recall_total += hits / len(relevant_set)

    count = len(cases)
    return LearningEvaluationReport(
        case_count=count,
        hit_rate_at_k=round(hit_count / count, 6),
        precision_at_k=round(precision_total / count, 6),
        recall_at_k=round(recall_total / count, 6),
        mean_reciprocal_rank=round(reciprocal_rank_total / count, 6),
        k=k,
    )
