"""Conservative, side-effect-free concurrency budgeting.

This module does not start workers or infer a universal memory threshold.
Additional concurrency requires an operator-supplied per-worker memory estimate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class WorkerBudget:
    requested_concurrency: int
    allowed_concurrency: int
    cpu_limit: int | None
    memory_limit: int | None
    memory_budget_bytes: int | None
    reason: str


def calculate_worker_budget(
    *,
    logical_cpu_count: int | None,
    memory_effective_available_bytes: int | None,
    requested_concurrency: int = 1,
    per_worker_memory_bytes: int | None = None,
    memory_reserve_bytes: int = 0,
) -> WorkerBudget:
    """Calculate a safe upper bound; never launches work or guesses memory costs."""
    for name, value, minimum in (
        ("requested_concurrency", requested_concurrency, 1),
        ("memory_reserve_bytes", memory_reserve_bytes, 0),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")

    if per_worker_memory_bytes is not None and (
        isinstance(per_worker_memory_bytes, bool)
        or not isinstance(per_worker_memory_bytes, int)
        or per_worker_memory_bytes <= 0
    ):
        raise ValueError("per_worker_memory_bytes must be a positive integer when supplied")

    cpu_limit = logical_cpu_count if isinstance(logical_cpu_count, int) and not isinstance(logical_cpu_count, bool) and logical_cpu_count > 0 else None
    memory_limit = (
        memory_effective_available_bytes
        if isinstance(memory_effective_available_bytes, int)
        and not isinstance(memory_effective_available_bytes, bool)
        and memory_effective_available_bytes >= 0
        else None
    )

    # Unknown resource data never increases the one-worker safe default.
    allowed = 1
    reasons = []
    if cpu_limit is None:
        reasons.append("CPU capacity unknown; concurrency held to one")
    else:
        allowed = min(requested_concurrency, cpu_limit)

    if requested_concurrency > 1:
        if memory_limit is None:
            allowed = 1
            reasons.append("effective available memory unknown; concurrency held to one")
        elif per_worker_memory_bytes is None:
            allowed = 1
            reasons.append("per-worker memory estimate not configured; concurrency held to one")
        else:
            budget = max(0, memory_limit - memory_reserve_bytes)
            memory_workers = budget // per_worker_memory_bytes
            allowed = min(allowed, memory_workers)
            if allowed < 1:
                allowed = 1
                reasons.append("configured memory budget cannot safely admit parallel work")
            else:
                reasons.append("bounded by CPU, requested concurrency, and configured memory budget")
    else:
        reasons.append("single-worker default")

    return WorkerBudget(
        requested_concurrency=requested_concurrency,
        allowed_concurrency=allowed,
        cpu_limit=cpu_limit,
        memory_limit=memory_limit,
        memory_budget_bytes=(
            max(0, memory_limit - memory_reserve_bytes)
            if memory_limit is not None and per_worker_memory_bytes is not None
            else None
        ),
        reason="; ".join(reasons),
    )


def worker_budget_to_dict(budget: WorkerBudget) -> dict[str, int | str | None]:
    """Return a JSON-serializable operator diagnostic."""
    return asdict(budget)
