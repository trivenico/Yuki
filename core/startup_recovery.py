"""Startup reconciliation for interrupted journaled sandbox executions.

This worker only cleans up containers for execution IDs already recorded in the
journal. It never replays commands. Any ambiguous Docker result remains pending
for a later recovery attempt or human investigation.
"""
from __future__ import annotations

from dataclasses import dataclass

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
) -> StartupRecoveryReport:
    """Reconcile every pending execution and persist confirmed cleanup outcomes."""
    try:
        execution_ids = journal.mark_interrupted_for_reconciliation()
    except ExecutionJournalError as exc:
        raise RuntimeError("Could not enumerate interrupted executions.") from exc

    items: list[RecoveryItem] = []
    cleaned = 0
    for execution_id in execution_ids:
        try:
            container = reconcile(execution_id)
            # Absence is an idempotent success; ambiguous Docker errors raise and
            # leave the durable record cleanup_required for the next startup.
            journal.transition(
                execution_id,
                "cleaned",
                result={
                    "recovery": "container_absent" if container.already_absent else "container_removed",
                    "container_inspected": container.inspected,
                    "container_removed": container.removed,
                },
                cleanup_confirmed=True,
            )
            cleaned += 1
            items.append(RecoveryItem(execution_id, "cleaned", container=container))
        except (ContainerRecoveryError, ExecutionJournalError, OSError, RuntimeError) as exc:
            items.append(RecoveryItem(
                execution_id,
                "pending",
                error=type(exc).__name__,
            ))

    return StartupRecoveryReport(
        discovered=len(execution_ids),
        cleaned=cleaned,
        pending=len(execution_ids) - cleaned,
        items=tuple(items),
    )
