"""Read-only health view across Yuki's provider and workflow journals.

The journals currently have no universal execution_id <-> operation_id
foreign key. This control plane deliberately reports correlation as unavailable
rather than guessing from provider names, timestamps, or request digests.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from automation.workflow_store import WorkflowRunStore
from providers.durable_provider_governance import SQLiteProviderGovernanceStore


class YukiRecoveryControlPlane:
    """Aggregate bounded recovery signals without mutating either journal."""

    def __init__(
        self, *, workflow_store: WorkflowRunStore,
        provider_store: SQLiteProviderGovernanceStore,
    ) -> None:
        if not isinstance(workflow_store, WorkflowRunStore):
            raise TypeError("workflow_store_required")
        if not isinstance(provider_store, SQLiteProviderGovernanceStore):
            raise TypeError("provider_store_required")
        self.workflow_store = workflow_store
        self.provider_store = provider_store

    @staticmethod
    def _stale(updated_at: Any, *, now: datetime, threshold_seconds: int) -> bool:
        try:
            if isinstance(updated_at, (int, float)):
                age = now.timestamp() - float(updated_at)
            elif isinstance(updated_at, str):
                stamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    return False
                age = now.timestamp() - stamp.astimezone(timezone.utc).timestamp()
            else:
                return False
            return age >= threshold_seconds
        except (TypeError, ValueError, OverflowError):
            return False

    def snapshot(
        self, *, limit: int = 100, stale_after_seconds: int = 300,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit_out_of_range")
        if type(stale_after_seconds) is not int or not 1 <= stale_after_seconds <= 604800:
            raise ValueError("stale_after_seconds_out_of_range")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("now_must_be_timezone_aware")

        provider_rows = self.provider_store.list_recovery_queue(limit=limit)
        workflow_rows = self.workflow_store.list_unresolved_operations(limit=limit)
        provider_items = [
            {
                **row,
                "stale": self._stale(
                    row.get("updated_at"), now=current,
                    threshold_seconds=stale_after_seconds,
                ),
                "correlation_status": "not_available",
            }
            for row in provider_rows
        ]
        workflow_items = [
            {
                "operation_id": row.operation_id,
                "task_id": row.task_id,
                "action": row.action,
                "provider_name": row.provider_name,
                "status": row.status,
                "version": row.version,
                "updated_at": row.updated_at,
                "stale": self._stale(
                    row.updated_at, now=current,
                    threshold_seconds=stale_after_seconds,
                ),
                "correlation_status": "not_available",
            }
            for row in workflow_rows
        ]
        correlations = self.provider_store.list_workflow_correlations(limit=limit)
        correlation_checks = []
        for link in correlations:
            execution = self.provider_store.get_execution(link["execution_id"])
            operation = self.workflow_store.get_operation(link["operation_id"])
            if execution is None or operation is None:
                status = "orphaned"
            elif execution["provider_id"] != link["provider_id"] or operation.provider_name != link["provider_id"]:
                status = "provider_mismatch"
            else:
                status = "verified"
            correlation_checks.append({
                **link, "status": status,
                "provider_execution_status": execution["status"] if execution else None,
                "workflow_operation_status": operation.status if operation else None,
            })
        provider_uncertain = sum(row["status"] == "uncertain" for row in provider_items)
        provider_running = sum(row["status"] == "running" for row in provider_items)
        workflow_reconcile = sum(row["status"] == "needs_reconciliation" for row in workflow_items)
        workflow_inflight = sum(row["status"] == "in_flight" for row in workflow_items)
        provider_stale = sum(row["stale"] for row in provider_items)
        workflow_stale = sum(row["stale"] for row in workflow_items)
        return {
            "generated_at": current.astimezone(timezone.utc).isoformat(),
            "read_only": True,
            "automatic_retry_permitted": False,
            "limits": {"per_journal": limit, "stale_after_seconds": stale_after_seconds},
            "provider": {
                "items": provider_items, "returned": len(provider_items),
                "running": provider_running, "uncertain": provider_uncertain,
                "stale": provider_stale,
            },
            "workflow": {
                "items": workflow_items, "returned": len(workflow_items),
                "in_flight": workflow_inflight,
                "needs_reconciliation": workflow_reconcile,
                "stale": workflow_stale,
            },
            "correlation": {
                "status": "available",
                "items": correlation_checks,
                "returned": len(correlation_checks),
                "matched": sum(row["status"] == "verified" for row in correlation_checks),
                "verified": sum(row["status"] == "verified" for row in correlation_checks),
                "unmatched": sum(row["status"] != "verified" for row in correlation_checks),
                "orphaned": sum(row["status"] == "orphaned" for row in correlation_checks),
                "provider_mismatch": sum(row["status"] == "provider_mismatch" for row in correlation_checks),
                "warning": "Only explicit durable links are inspected; unlinked records are not inferred.",
            },
            "health": {
                "status": "attention_required" if (
                    provider_uncertain or workflow_reconcile or provider_stale or workflow_stale
                ) else "no_reported_recovery_items",
                "provider_uncertain": provider_uncertain,
                "workflow_needs_reconciliation": workflow_reconcile,
                "stale_provider": provider_stale,
                "stale_workflow": workflow_stale,
            },
        }


__all__ = ["YukiRecoveryControlPlane"]
