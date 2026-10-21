"""Startup reconciliation for interrupted journaled sandbox executions.

This worker only cleans up containers for execution IDs already recorded in the
journal. It never replays commands. Any ambiguous Docker result remains pending
for a later recovery attempt or human investigation.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from core.container_recovery import (
    ContainerRecoveryError,
    ContainerRecoveryResult,
    reconcile_execution_container,
)
from core.execution_journal import ExecutionJournalError, SQLiteExecutionJournal


@dataclass(frozen=True)
class RecoveryItem:
    execution_id: str
    outcome: str
    container: ContainerRecoveryResult | None = None
    error: str | None = None


@dataclass(frozen=True)
class StartupRecoveryReport:
    discovered: int
    cleaned: int
    pending: int
    items: tuple[RecoveryItem, ...]


def recover_interrupted_executions(
    journal: SQLiteExecutionJournal,
    *,
    reconcile=reconcile_execution_container,
    worker_id: str | None = None,
    lease_seconds: int = 3600,
) -> StartupRecoveryReport:
    """Reconcile pending executions claimed atomically by this recovery worker."""
    worker_id = worker_id or f"recovery-{uuid4().hex}"
    try:
        execution_ids = journal.mark_interrupted_for_reconciliation()
    except ExecutionJournalError as exc:
        raise RuntimeError("Could not enumerate interrupted executions.") from exc

    items: list[RecoveryItem] = []
    cleaned = 0
    for execution_id in execution_ids:
        try:
            claimed = journal.claim_recovery(execution_id, worker_id, lease_seconds)
        except ExecutionJournalError as exc:
            items.append(RecoveryItem(execution_id, "pending", error=type(exc).__name__))
            continue
        if not claimed:
            items.append(RecoveryItem(execution_id, "claimed_by_other_worker"))
            continue
        try:
            container = reconcile(execution_id)
            journal.transition(execution_id, "cleaned", result={
                "recovery": "container_absent" if container.already_absent else "container_removed",
                "container_inspected": container.inspected,
                "container_removed": container.removed,
            }, cleanup_confirmed=True)
            cleaned += 1
            items.append(RecoveryItem(execution_id, "cleaned", container=container))
        except (ContainerRecoveryError, ExecutionJournalError, OSError, RuntimeError) as exc:
            items.append(RecoveryItem(execution_id, "pending", error=type(exc).__name__))
        finally:
            try:
                journal.release_recovery_claim(execution_id, worker_id)
            except ExecutionJournalError:
                pass  # A later worker can recover an expired lease.

    return StartupRecoveryReport(
        discovered=len(execution_ids),
        cleaned=cleaned,
        pending=len(execution_ids) - cleaned,
        items=tuple(items),
    )
