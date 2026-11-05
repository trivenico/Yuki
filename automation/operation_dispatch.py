"""Guarded operation dispatch over the durable journal and provider registry.

The request is supplied by the caller because the journal intentionally stores
only its digest. A digest match is required before any provider call. Ambiguous
transport/provider outcomes are recorded for reconciliation, never retried here.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from automation.operation_providers import (
    OperationProviderRegistry,
    ProviderCapabilityError,
)
from automation.workflow_store import WorkflowConflict


class OperationDispatchError(RuntimeError):
    """Dispatch was refused or its outcome could not be safely confirmed."""


class OperationDispatcher:
    def __init__(self, runtime: Any, providers: OperationProviderRegistry) -> None:
        self.runtime = runtime
        self.providers = providers

    @staticmethod
    def _digest(request: Mapping[str, Any]) -> str:
        try:
            encoded = json.dumps(
                request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("request must be JSON-serializable.") from exc
        if len(encoded.encode("utf-8")) > 16 * 1024:
            raise ValueError("request exceeds the 16384-byte limit.")
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def dispatch(
        self, *, actor_id: str, worker_id: str, operation_id: str,
        provider_name: str, request: Mapping[str, Any],
    ):
        if not isinstance(request, Mapping):
            raise ValueError("request must be an object.")
        operation = self.runtime._authorized_operation(
            self.runtime._actor(actor_id), operation_id, "task:operation:dispatch"
        )
        if operation.status != "prepared":
            raise OperationDispatchError("only prepared operations can be dispatched.")
        if self._digest(request) != operation.request_digest:
            raise WorkflowConflict("dispatch request does not match the prepared operation.")
        try:
            registered = self.providers.require_idempotent_dispatch(provider_name)
        except (KeyError, ProviderCapabilityError) as exc:
            raise OperationDispatchError("provider is unavailable or lacks safe idempotency support.") from exc

        inflight = self.runtime.mark_operation_in_flight(
            actor_id=actor_id, operation_id=operation_id, worker_id=worker_id,
            expected_version=operation.version,
        )
        try:
            response = registered.provider.execute(
                inflight.action, request, idempotency_key=inflight.idempotency_key,
            )
            if not isinstance(response, Mapping):
                raise TypeError("provider response must be an object.")
            status = response.get("status")
            if status != "succeeded":
                status = "needs_reconciliation"
            reference = response.get("provider_reference")
            if reference is not None and (
                not isinstance(reference, str) or not reference.strip() or len(reference) > 500
            ):
                status, reference = "needs_reconciliation", None
            safe_outcome = {"provider_status": status}
            if status == "needs_reconciliation":
                safe_outcome["reason"] = "provider outcome was not conclusively successful"
            resolved = self.runtime.record_operation_outcome(
                actor_id=actor_id, operation_id=operation_id,
                expected_version=inflight.version, status=status,
                provider_reference=reference, outcome=safe_outcome,
            )
            if status != "succeeded":
                raise OperationDispatchError("provider outcome requires reconciliation.")
            return resolved
        except OperationDispatchError:
            raise
        except Exception as exc:
            # Once dispatch has started, an exception may follow a successful
            # remote side effect. Record uncertainty; never retry automatically.
            try:
                self.runtime.record_operation_outcome(
                    actor_id=actor_id, operation_id=operation_id,
                    expected_version=inflight.version, status="needs_reconciliation",
                    outcome={"reason": "provider call raised; remote outcome is unknown"},
                )
            except Exception:
                # A process crash or concurrent state change leaves the durable
                # in_flight record for lease-expiry recovery.
                pass
            raise OperationDispatchError("provider outcome is uncertain; reconciliation is required.") from exc
