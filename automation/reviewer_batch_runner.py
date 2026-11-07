"""Controlled sequential execution for provisioned reviewer batches."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from automation.reviewer_contracts import ReviewTask
from automation.reviewer_execution import (
    ReviewerExecutionAdapter, ReviewerExecutionOutcome,
)
from automation.reviewer_orchestrator import ProvisionedReviewerTask


@dataclass(frozen=True)
class ReviewerBatchExecution:
    outcomes: tuple[ReviewerExecutionOutcome, ...]
    failed_task_ids: tuple[str, ...]


def execute_reviewer_batch(
    adapter: ReviewerExecutionAdapter,
    *,
    items: Iterable[tuple[ProvisionedReviewerTask, ReviewTask, int]],
    actor_id_for_reviewer: Callable[[str], str],
    executor: Callable,
    max_tasks: int = 25,
) -> ReviewerBatchExecution:
    """Execute explicitly supplied tasks in order; never schedules background work.

    Each item carries the current ledger version. A failure is isolated to that
    item and subsequent tasks continue. Credentials remain in host memory.
    """
    if not isinstance(adapter, ReviewerExecutionAdapter):
        raise TypeError("reviewer_execution_adapter_required")
    if not callable(actor_id_for_reviewer) or not callable(executor):
        raise TypeError("trusted_actor_resolver_and_executor_required")
    if type(max_tasks) is not int or not 1 <= max_tasks <= 100:
        raise ValueError("invalid_reviewer_batch_limit")
    batch = tuple(items)
    if not batch or len(batch) > max_tasks:
        raise ValueError("reviewer_batch_size_out_of_range")
    # Validate the complete batch before invoking any executor. A duplicate or
    # malformed later item must not leave earlier tasks partially executed.
    seen = set()
    validated = []
    for item in batch:
        if not isinstance(item, tuple) or len(item) != 3:
            raise TypeError("reviewer_batch_item_must_be_task_tuple")
        provisioned, task, version = item
        if not isinstance(provisioned, ProvisionedReviewerTask) or not isinstance(task, ReviewTask):
            raise TypeError("invalid_reviewer_batch_task")
        if provisioned.task_id in seen:
            raise ValueError("duplicate_reviewer_batch_task")
        seen.add(provisioned.task_id)
        if type(version) is not int or version < 1:
            raise ValueError("invalid_reviewer_task_version")
        validated.append((provisioned, task, version))

    outcomes = []
    failures = []
    for provisioned, task, version in validated:
    return ReviewerBatchExecution(tuple(outcomes), tuple(failures))


__all__ = ["ReviewerBatchExecution", "execute_reviewer_batch"]
