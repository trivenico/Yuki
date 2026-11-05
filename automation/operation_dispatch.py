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

    @staticmethod
    def _validate_provider_response(response: Any, *, lookup: bool) -> Mapping[str, Any]:
        """Validate a deliberately small provider response contract.

        Provider responses are untrusted input. Reject unknown fields, oversized
        payloads, malformed statuses and invalid references before persistence.
        """
        if not isinstance(response, Mapping):
            raise OperationDispatchError("provider response must be an object.")
        allowed = {"status", "provider_reference"}
        if lookup:
            allowed.add("idempotency_key")
        if set(response.keys()) - allowed or "status" not in response:
            raise OperationDispatchError("provider response contains unsupported fields.")
        try:
            encoded = json.dumps(
                dict(response), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise OperationDispatchError("provider response is not valid JSON data.") from exc
        if len(encoded) > 4096:
            raise OperationDispatchError("provider response exceeds the 4096-byte limit.")
        status = response.get("status")
        if not isinstance(status, str) or status not in {
            "succeeded", "pending", "failed", "not_found",
        }:
            raise OperationDispatchError("provider response has an unsupported status.")
        reference = response.get("provider_reference")
        if reference is not None and (
            not isinstance(reference, str) or not reference.strip()
            or len(reference) > 500 or "\\x00" in reference
        ):
            raise OperationDispatchError("provider response has an invalid reference.")
        if status == "succeeded" and reference is None:
            raise OperationDispatchError("successful provider response requires a reference.")
        if lookup:
            key = response.get("idempotency_key")
            if not isinstance(key, str) or not key.strip() or len(key) > 200 or "\\x00" in key:
                raise OperationDispatchError("provider lookup response requires a valid idempotency key.")
        return response

    def reconcile(self, *, actor_id: str, operation_id: str, provider_name: str):
        """Use provider lookup only; absence or ambiguity never clears uncertainty."""
        operation = self.runtime._authorized_operation(
            self.runtime._actor(actor_id), operation_id, "task:operation:resolve",
        )
        if operation.status != "needs_reconciliation":
            raise OperationDispatchError("only uncertain operations can be reconciled.")
        if not operation.provider_name or provider_name != operation.provider_name:
            raise OperationDispatchError("provider does not match the operation's recorded dispatch provider.")
        try:
            registered = self.providers.require_operation_lookup(operation.provider_name)
        except (KeyError, ProviderCapabilityError) as exc:
            raise OperationDispatchError("provider lookup is unavailable.") from exc
        try:
            response = registered.provider.lookup(idempotency_key=operation.idempotency_key)
        except Exception as exc:
            raise OperationDispatchError("provider lookup failed; operation remains uncertain.") from exc
        if response is None:
            return operation
        response = self._validate_provider_response(response, lookup=True)
        if response.get("status") != "succeeded":
            return operation
        if response.get("idempotency_key") != operation.idempotency_key:
            raise OperationDispatchError("provider lookup identity mismatch; operation remains uncertain.")
        reference = response.get("provider_reference")
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 500:
            raise OperationDispatchError("provider lookup lacks a valid reference; operation remains uncertain.")
        return self.runtime.resolve_reconciled_operation(
            actor_id=actor_id, operation_id=operation_id,
            expected_version=operation.version, provider_reference=reference,
        )

    def dispatch(
        self, *, actor_id: str, worker_id: str, operation_id: str,
        provider_name: str, request: Mapping[str, Any],
    ):
        if not isinstance(request, Mapping):
            raise ValueError("request must be an object.")
        operation = self.runtime.authorize_operation_dispatch(
            actor_id=actor_id, operation_id=operation_id,
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
            provider_name=provider_name, expected_version=operation.version,
        )
        try:
            response = registered.provider.execute(
                inflight.action, request, idempotency_key=inflight.idempotency_key,
            )
            response = self._validate_provider_response(response, lookup=False)
            status = "succeeded" if response.get("status") == "succeeded" else "needs_reconciliation"
            reference = response.get("provider_reference") if status == "succeeded" else None
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
