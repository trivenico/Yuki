"""Lease-aware recovery orchestration for supervised reviewer workers.

Worker heartbeat expiry is only a candidate signal. Task recovery is delegated
to ReviewerTaskLedger, which atomically verifies the task version and expired
reviewer lease, rotates the lease generation, records audit events, and
requeues the durable dispatch in the same transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from automation.reviewer_ledger import ReviewerLedgerConflict, ReviewerTaskLedger
from automation.reviewer_leases import ReviewerLeaseConflict
from automation.reviewer_worker_registry import (
    ReviewerWorkerRegistry, ReviewerWorkerRegistryConflict,
)


@dataclass(frozen=True)
class ReviewerRecoveryOutcome:
    task_id: str
    previous_reviewer_agent_id: str
    replacement_reviewer_agent_id: str | None
    state: str
    reason_code: str


@dataclass(frozen=True)
class ReviewerRecoveryReport:
    expired_worker_instances: int
    expired_worker_ids: tuple[str, ...]
    recovered_tasks: int
    deferred_tasks: int
    outcomes: tuple[ReviewerRecoveryOutcome, ...]


class ReviewerWorkerRecoverySupervisor:
    """Reconcile expired worker identities with expired task leases.

    replacement_resolver receives a task projection and the expired reviewer
    ID, and must return an authorized, distinct reviewer identity or None.
    It does not receive credentials or lease tokens.
    """

    def __init__(
        self,
        registry: ReviewerWorkerRegistry,
        ledger: ReviewerTaskLedger,
        *,
        actor_id: str,
        worker_ids: Iterable[str],
        replacement_resolver: Callable[[dict, str], str | None],
    ) -> None:
        if not all(callable(getattr(registry, name, None)) for name in
                   ("expire_stale", "list_workers")):
            raise TypeError("reviewer_worker_registry_interface_required")
        if not all(callable(getattr(ledger, name, None)) for name in
                   ("list_expired_in_progress", "recover_in_progress")):
            raise TypeError("reviewer_task_ledger_interface_required")
        if not isinstance(actor_id, str) or not actor_id:
            raise ValueError("reviewer_recovery_actor_required")
        ids = tuple(dict.fromkeys(worker_ids))
        if not ids or len(ids) > 500 or any(not isinstance(x, str) or not x for x in ids):
            raise ValueError("reviewer_recovery_worker_ids_required")
        if not callable(replacement_resolver):
            raise TypeError("trusted_reviewer_replacement_resolver_required")
        self.registry = registry
        self.ledger = ledger
        self.actor_id = actor_id
        self.worker_ids = ids
        self.replacement_resolver = replacement_resolver

    def run_once(self, *, limit: int = 100) -> ReviewerRecoveryReport:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("invalid_reviewer_recovery_limit")

        expired_ids: set[str] = set()
        expired_instances = 0
        for worker_id in self.worker_ids:
            expired_instances += self.registry.expire_stale(
                actor_id=self.actor_id, worker_id=worker_id,
            )
            statuses = self.registry.list_workers(
                actor_id=self.actor_id, worker_id=worker_id,
            )
            if any(item.state == "expired" for item in statuses):
                expired_ids.add(worker_id)

        if not expired_ids:
            return ReviewerRecoveryReport(0, (), 0, 0, ())

        # Only task leases already expired are eligible. A dead worker with a
        # still-live task lease is deferred until the task lease expires.
        candidates = self.ledger.list_expired_in_progress(
            actor_id=self.actor_id, limit=limit,
        )
        recovered = deferred = 0
        outcomes: list[ReviewerRecoveryOutcome] = []
        for task in candidates:
            previous = task["reviewer_agent_id"]
            if previous not in expired_ids:
                continue
            replacement = self.replacement_resolver(dict(task), previous)
            if replacement is None:
                deferred += 1
                outcomes.append(ReviewerRecoveryOutcome(
                    task["task_id"], previous, None, "deferred",
                    "no_eligible_replacement",
                ))
                continue
            if not isinstance(replacement, str) or not replacement or replacement == previous:
                raise ValueError("invalid_reviewer_replacement_identity")
            try:
                result = self.ledger.recover_in_progress(
                    actor_id=self.actor_id,
                    task_id=task["task_id"],
                    expected_version=task["version"],
                    replacement_reviewer_agent_id=replacement,
                    reason="reviewer_worker_lease_expired",
                )
                recovered += 1
                outcomes.append(ReviewerRecoveryOutcome(
                    task["task_id"], previous, replacement, result["state"],
                    "expired_worker_and_task_lease_recovered",
                ))
            except (ReviewerLedgerConflict, ReviewerLeaseConflict, ReviewerWorkerRegistryConflict):
                # Version/lease changed concurrently: never force a stale recovery.
                deferred += 1
                outcomes.append(ReviewerRecoveryOutcome(
                    task["task_id"], previous, replacement, "deferred",
                    "task_or_lease_changed_during_recovery",
                ))
        return ReviewerRecoveryReport(
            expired_worker_instances=expired_instances,
            expired_worker_ids=tuple(sorted(expired_ids)),
            recovered_tasks=recovered,
            deferred_tasks=deferred,
            outcomes=tuple(outcomes),
        )


__all__ = [
    "ReviewerWorkerRecoverySupervisor", "ReviewerRecoveryReport",
    "ReviewerRecoveryOutcome",
]
