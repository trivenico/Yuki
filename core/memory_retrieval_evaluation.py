"""Deterministic, dependency-free metrics for offline memory retrieval evaluation.

These metrics evaluate ranking quality and isolation invariants. They do not
change memory, authorize access, or interpret retrieval scores as truth.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence


def _positive_k(k: int) -> int:
    if type(k) is not int or k < 1:
        raise ValueError("k_must_be_positive_integer")
    return k


def recall_at_k(retrieved_ids: Sequence[str], relevant_ids: Iterable[str], k: int = 5) -> float:
    """Fraction of known relevant IDs found in the first k results."""
    k = _positive_k(k)
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    return len(set(retrieved_ids[:k]) & relevant) / len(relevant)


def reciprocal_rank(retrieved_ids: Sequence[str], relevant_ids: Iterable[str]) -> float:
    """Reciprocal rank of the first relevant result, or zero if absent."""
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    for rank, memory_id in enumerate(retrieved_ids, start=1):
        if memory_id in relevant:
            return 1.0 / rank
    return 0.0


def mean_reciprocal_rank(
    result_sets: Iterable[tuple[Sequence[str], Iterable[str]]],
) -> float:
    """Mean reciprocal rank across independent query result/relevance pairs."""
    scores = [reciprocal_rank(results, relevant) for results, relevant in result_sets]
    return sum(scores) / len(scores) if scores else 0.0


def ndcg_at_k(
    retrieved_ids: Sequence[str],
    relevance_by_id: Mapping[str, float],
    k: int = 5,
) -> float:
    """Normalized discounted cumulative gain for graded relevance labels."""
    k = _positive_k(k)
    if not isinstance(relevance_by_id, Mapping):
        raise ValueError("relevance_by_id_must_be_a_mapping")
    for value in relevance_by_id.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("relevance_must_be_numeric")
        if not math.isfinite(value) or value < 0:
            raise ValueError("relevance_must_be_finite_and_nonnegative")

    # Subtracting the maximum relevance is a common positive scale factor
    # across all gains, so nDCG is unchanged while exponent overflow is avoided.
    max_relevance = max(relevance_by_id.values(), default=0.0)

    def gain(memory_id: str, rank: int) -> float:
        relevance = relevance_by_id.get(memory_id, 0.0)
        scaled_gain = math.exp2(relevance - max_relevance)
        return scaled_gain / math.log2(rank + 1)

    dcg = sum(gain(memory_id, rank) for rank, memory_id in enumerate(retrieved_ids[:k], 1))
    ideal = sorted(relevance_by_id.values(), reverse=True)[:k]
    idcg = sum(
        math.exp2(value - max_relevance) / math.log2(rank + 1)
        for rank, value in enumerate(ideal, 1)
    )
    # Zero relevance has equal gain for every item in this scaled form.
    # Standard nDCG convention reports zero when there is no positive gain.
    if max_relevance == 0:
        return 0.0
    return dcg / idcg if idcg else 0.0


def audit_retrieval(
    retrieved_ids: Sequence[str],
    *,
    authorized_ids: Iterable[str],
    active_ids: Iterable[str],
) -> dict[str, int | bool]:
    """Count authorization and stale-record leaks; duplicate IDs are reported."""
    authorized = set(authorized_ids)
    active = set(active_ids)
    seen: set[str] = set()
    duplicate_count = 0
    unauthorized_count = 0
    stale_count = 0
    for memory_id in retrieved_ids:
        if memory_id in seen:
            duplicate_count += 1
        seen.add(memory_id)
        if memory_id not in authorized:
            unauthorized_count += 1
        if memory_id not in active:
            stale_count += 1
    return {
        "unauthorized_count": unauthorized_count,
        "stale_count": stale_count,
        "duplicate_count": duplicate_count,
        "permission_leakage_free": unauthorized_count == 0,
        "stale_record_leakage_free": stale_count == 0,
    }
