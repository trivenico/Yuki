"""Evidence-backed reconciliation across provider and workflow journals.

The two SQLite stores cannot share one atomic transaction. This service uses
an idempotent, retryable two-journal sequence: provider resolution is committed
first, then the workflow operation. A retry verifies the immutable provider
resolution before completing the workflow side. It never dispatches provider work.
"""
from __future__ import annotations

import re
from typing import Any

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowTransitionError
from providers.durable_provider_governance import SQLiteProviderGovernanceStore


class ProviderWorkflowReconciliation:
    """Correlate an uncertain provider execution with one workflow operation."""

    def __init__(
        self, *, task_runtime: TaskRuntime,
        provider_store: SQLiteProviderGovernanceStore,
    ) -> None:
        if not isinstance(task_runtime, TaskRuntime):
            raise TypeError("task_runtime_required")
        if not isinstance(provider_store, SQLiteProviderGovernanceStore):
            raise TypeError("provider_store_required")
        self.task_runtime = task_runtime
        self.provider_store = provider_store

    def reconcile_success(
        self, *, actor_id: str, operation_id: str, execution_id: str,
        expected_version: int, provider_reference: str,
        evidence_source: str, evidence_sha256: str, resolver_id: str,
    ):
        """Record provider-confirmed success in both journals.

        The caller must authenticate the provider evidence before invoking
        this method. The evidence digest is an integrity reference, not proof
        of authenticity. Repeating the same call safely finishes a partial
        two-store reconciliation; conflicting evidence is rejected.
        """
        actor = self.task_runtime._actor(actor_id)
        operation = self.task_runtime._authorized_operation(
            actor, operation_id, "task:operation:resolve",
        )
        if operation.version != expected_version and operation.status != "succeeded":
            from automation.workflow_store import WorkflowConflict
            raise WorkflowConflict("operation version changed; reload before reconciliation.")
        if operation.status not in {"needs_reconciliation", "succeeded"}:
            raise WorkflowTransitionError("workflow_operation_not_awaiting_reconciliation")
        if operation.provider_name != evidence_source:
            raise WorkflowTransitionError("evidence_source_does_not_match_workflow_provider")
        execution = self.provider_store.get_execution(execution_id)
        if execution is None:
            raise KeyError("provider execution not found.")
        if execution["provider_id"] != operation.provider_name:
            raise WorkflowTransitionError("provider_execution_does_not_match_workflow_provider")
        if not isinstance(resolver_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", resolver_id):
            raise ValueError("resolver_id_invalid")
        if not isinstance(provider_reference, str) or not provider_reference.strip():
            raise ValueError("provider_reference_required")
        if not isinstance(evidence_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
            raise ValueError("evidence_sha256_invalid")

        resolutions = self.provider_store.list_resolutions(execution_id=execution_id)
        if execution["status"] == "uncertain":
            self.provider_store.resolve_uncertain_execution(
                execution_id, outcome="succeeded", evidence_source=evidence_source,
                evidence_sha256=evidence_sha256, provider_reference=provider_reference,
                resolver_id=resolver_id,
            )
        elif execution["status"] == "succeeded" and resolutions:
            existing = resolutions[0]
            expected = (evidence_source, evidence_sha256, provider_reference, resolver_id, "succeeded")
            actual = (
                existing["evidence_source"], existing["evidence_sha256"],
                existing["provider_reference"], existing["resolver_id"], existing["outcome"],
            )
            if actual != expected:
                raise WorkflowTransitionError("provider_execution_resolved_with_different_evidence")
        else:
            raise WorkflowTransitionError("provider_execution_not_reconcilable")

        # If a prior attempt committed the provider journal but failed before
        # the workflow journal, complete only the workflow transition here.
        current = self.task_runtime.store.get_operation(operation_id)
        if current is None:
            raise KeyError("operation not found.")
        if current.status == "succeeded":
            expected_outcome = {
                "reconciliation": "provider_lookup_confirmed_success",
                "evidence_source": evidence_source,
                "evidence_sha256": evidence_sha256,
            }
            if current.provider_reference != provider_reference or current.outcome != expected_outcome:
                raise WorkflowTransitionError("workflow_provider_resolution_conflict")
            return current
        if current.status != "needs_reconciliation":
            raise WorkflowTransitionError("workflow_operation_not_awaiting_reconciliation")
        return self.task_runtime.resolve_reconciled_operation(
            actor_id=actor, operation_id=operation_id,
            expected_version=current.version, provider_reference=provider_reference,
            evidence_source=evidence_source, evidence_digest=evidence_sha256,
        )


__all__ = ["ProviderWorkflowReconciliation"]
