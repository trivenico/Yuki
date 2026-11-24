"""Authorized entry point for durable external-operation intents.

This service composes trusted task identity verification with the PostgreSQL
operation journal. It performs no provider network calls.
"""
from __future__ import annotations

from automation.postgres_external_operation_store import PostgresExternalOperationStore
from automation.task_runtime import TaskRuntime


class AuthorizedExternalActionService:
    """Require one shared PostgreSQL security boundary for operation lifecycle."""

    def __init__(self, task_runtime: TaskRuntime,
                 operation_store: PostgresExternalOperationStore):
        if not isinstance(task_runtime, TaskRuntime):
            raise TypeError("trusted_task_runtime_required")
        if not isinstance(operation_store, PostgresExternalOperationStore):
            raise TypeError("postgres_external_operation_store_required")
        if (
            task_runtime.authorization_gateway is None
            or task_runtime.workload_credential_verifier is None
            or not task_runtime.require_workload_delegation
            or task_runtime.delegation_authority is None
        ):
            raise ValueError("authorized_external_actions_require_gateway_verifier_and_delegation")
        workflow_identity = getattr(task_runtime.store, "database_identity", None)
        delegation_identity = getattr(task_runtime.delegation_authority, "database_identity", None)
        operation_identity = getattr(operation_store, "database_identity", None)
        if (
            not isinstance(workflow_identity, str) or not workflow_identity
            or workflow_identity != delegation_identity
            or workflow_identity != operation_identity
        ):
            raise ValueError("authorized_external_action_repositories_must_share_database")
        self.task_runtime = task_runtime
        self.operation_store = operation_store

    def _context(self, *, task_id: str, worker_id: str,
                 credential: str, action_name: str):
        return self.task_runtime.verify_workload_context(
            task_id=task_id, worker_id=worker_id,
            credential=credential, action=action_name,
        )

    def create_intent(self, *, task_id: str, worker_id: str, credential: str,
                      step_id: str, action_name: str, action_version: str,
                      idempotency_key: str, input_digest: str):
        context = self._context(
            task_id=task_id, worker_id=worker_id,
            credential=credential, action_name=action_name,
        )
        return self.operation_store.create_authorized(
            context, step_id=step_id, action_name=action_name,
            action_version=action_version, idempotency_key=idempotency_key,
            input_digest=input_digest,
        )

    def claim_next(self, *, task_id: str, worker_id: str, credential: str,
                   action_name: str, lease_seconds: int = 60):
        context = self._context(
            task_id=task_id, worker_id=worker_id,
            credential=credential, action_name=action_name,
        )
        return self.operation_store.claim_next_authorized(
            context, lease_seconds=lease_seconds,
        )

    def authorize_dispatch(self, *, task_id: str, worker_id: str, credential: str,
                           action_name: str, operation_id: str,
                           operation_lease_generation: str, expected_version: int):
        context = self._context(
            task_id=task_id, worker_id=worker_id,
            credential=credential, action_name=action_name,
        )
        return self.operation_store.authorize_dispatch(
            context, operation_id, lease_generation=operation_lease_generation,
            expected_version=expected_version,
        )

    def complete(self, *, task_id: str, worker_id: str, credential: str,
                 action_name: str, operation_id: str,
                 operation_lease_generation: str, expected_version: int,
                 result_digest: str, provider_reference: str | None = None):
        context = self._context(
            task_id=task_id, worker_id=worker_id,
            credential=credential, action_name=action_name,
        )
        return self.operation_store.complete_authorized(
            context, operation_id, lease_generation=operation_lease_generation,
            expected_version=expected_version, result_digest=result_digest,
            provider_reference=provider_reference,
        )

    def mark_unknown(self, *, task_id: str, worker_id: str, credential: str,
                     action_name: str, operation_id: str,
                     operation_lease_generation: str, expected_version: int,
                     reason_code: str):
        context = self._context(
            task_id=task_id, worker_id=worker_id,
            credential=credential, action_name=action_name,
        )
        return self.operation_store.mark_unknown_authorized(
            context, operation_id, lease_generation=operation_lease_generation,
            expected_version=expected_version, reason_code=reason_code,
        )

    def fail_terminal(self, *, task_id: str, worker_id: str, credential: str,
                      action_name: str, operation_id: str,
                      operation_lease_generation: str, expected_version: int,
                      error_code: str):
        context = self._context(
            task_id=task_id, worker_id=worker_id,
            credential=credential, action_name=action_name,
        )
        return self.operation_store.fail_terminal_authorized(
            context, operation_id, lease_generation=operation_lease_generation,
            expected_version=expected_version, error_code=error_code,
        )

    def get(self, *, task_id: str, worker_id: str, credential: str,
            action_name: str, operation_id: str):
        context = self._context(
            task_id=task_id, worker_id=worker_id,
            credential=credential, action_name=action_name,
        )
        return self.operation_store.get_authorized(context, operation_id)

    def list_events(self, *, task_id: str, worker_id: str, credential: str,
                    action_name: str, operation_id: str):
        context = self._context(
            task_id=task_id, worker_id=worker_id,
            credential=credential, action_name=action_name,
        )
        return self.operation_store.list_events_authorized(context, operation_id)
