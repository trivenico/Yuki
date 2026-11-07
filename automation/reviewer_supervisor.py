"""Read-only reviewer lifecycle supervision and stale-claim detection."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from automation.reviewer_ledger import ReviewerTaskLedger


@dataclass(frozen=True)
class ReviewerTaskHealth:
    task_id: str
    state: str
    version: int
    stale: bool
    age_seconds: float
    recommended_action: str


@dataclass(frozen=True)
class ReviewerBatchHealth:
    checked_at: str
    tasks: tuple[ReviewerTaskHealth, ...]
    stale_task_ids: tuple[str, ...]
    submitted_count: int
    in_progress_count: int
    assigned_count: int


def inspect_reviewer_batch(
    ledger: ReviewerTaskLedger,
    *,
    actor_id: str,
    task_ids: Iterable[str],
    stale_after_seconds: int = 900,
    now: datetime | None = None,
    max_tasks: int = 100,
) -> ReviewerBatchHealth:
    """Inspect tasks through the ledger's authorized, integrity-checked read API.

    This function intentionally never reclaims or mutates a task. The current
    ledger lifecycle has no durable lease-expiry transition, so automatic
    reassignment would require a schema/state-machine migration first.
    """
    if not isinstance(ledger, ReviewerTaskLedger):
        raise TypeError("reviewer_task_ledger_required")
    if type(stale_after_seconds) is not int or not 30 <= stale_after_seconds <= 86400:
        raise ValueError("invalid_stale_threshold")
    if type(max_tasks) is not int or not 1 <= max_tasks <= 100:
        raise ValueError("invalid_reviewer_inspection_limit")
    instant = now or datetime.now(timezone.utc)
    if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("timezone_aware_inspection_time_required")
    ids = tuple(task_ids)
    if not ids or len(ids) > max_tasks or any(not isinstance(x, str) or not x.strip() for x in ids):
        raise ValueError("invalid_reviewer_task_id_collection")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_reviewer_task_id")
    threshold = timedelta(seconds=stale_after_seconds)
    health = []
    for task_id in ids:
        record = ledger.get(actor_id=actor_id, task_id=task_id)
        try:
            updated = datetime.fromisoformat(record["updated_at"])
            if updated.tzinfo is None or updated.utcoffset() is None:
                raise ValueError("naive_timestamp")
        except Exception as exc:
            raise ValueError("reviewer_ledger_timestamp_invalid") from exc
        age = max(0.0, (instant - updated).total_seconds())
        stale = record["state"] == "in_progress" and instant - updated > threshold
        if record["state"] == "submitted":
            action = "none"
        elif stale:
            action = "operator_review_required"
        elif record["state"] == "assigned":
            action = "await_reviewer_claim"
        else:
            action = "await_reviewer_completion"
        health.append(ReviewerTaskHealth(
            task_id=task_id, state=record["state"], version=record["version"],
            stale=stale, age_seconds=age, recommended_action=action,
        ))
    values = tuple(health)
    return ReviewerBatchHealth(
        checked_at=instant.isoformat(), tasks=values,
        stale_task_ids=tuple(x.task_id for x in values if x.stale),
        submitted_count=sum(x.state == "submitted" for x in values),
        in_progress_count=sum(x.state == "in_progress" for x in values),
        assigned_count=sum(x.state == "assigned" for x in values),
    )


__all__ = ["ReviewerTaskHealth", "ReviewerBatchHealth", "inspect_reviewer_batch"]
