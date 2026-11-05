"""Durable governed provider dispatch backed by shared SQLite reservations."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping

from providers.durable_provider_governance import (
    DurableBudgetExceeded, DurableCircuitOpen, DurableDuplicateExecution,
    DurableIdempotencyConflict, SQLiteProviderGovernanceStore,
)
from providers.provider_execution import ProviderCancelled


class DurableProviderAdmissionRejected(RuntimeError):
    """Durable provider admission denied execution."""


class DurableProviderDuplicate(RuntimeError):
    """Request was already admitted; caller must inspect the original execution."""

    def __init__(self, execution_id: str, status: str):
        self.execution_id = execution_id
        self.status = status
        super().__init__("provider_execution_already_exists")


class DurableProviderLeaseLost(RuntimeError):
    """Provider completed but its durable reservation was no longer active."""


class DurableGovernedProviderExecutor:
    """Execute one normalized adapter call under a durable provider lease.

    The store must be shared by all processes that are expected to coordinate.
    No retry or provider fallback is performed.
    """

    def __init__(
        self, *, provider_id: str, agent_id: str,
        adapter: Callable[..., Mapping[str, Any]],
        governance_store: SQLiteProviderGovernanceStore,
    ) -> None:
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError("provider_id_required")
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id_required")
        if not callable(adapter):
            raise TypeError("provider_adapter_must_be_callable")
        if not isinstance(governance_store, SQLiteProviderGovernanceStore):
            raise TypeError("durable_governance_store_required")
        timeout = getattr(adapter, "default_timeout_seconds", None)
        if timeout is not None and governance_store.lease_timeout_seconds <= timeout:
            raise ValueError("lease_timeout_must_exceed_adapter_timeout")
        self.provider_id = provider_id
        self.agent_id = agent_id
        self.adapter = adapter
        self.governance_store = governance_store

    @staticmethod
    def _request_fingerprint(request: Mapping[str, Any]) -> str:
        try:
            canonical = json.dumps(
                request, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError("provider_request_not_canonical_json") from exc
        return hashlib.sha256(canonical).hexdigest()

    def __call__(self, *, _idempotency_key: str | None = None, **request: Any) -> Mapping[str, Any]:
        validate = getattr(self.adapter, "validate_inputs", None)
        if callable(validate):
            validate(
                request.get("messages"), request.get("tools"),
                request.get("tool_choice", "auto"),
            )
        try:
            fingerprint = self._request_fingerprint(request) if _idempotency_key is not None else None
            lease = self.governance_store.acquire(
                provider_id=self.provider_id, agent_id=self.agent_id,
                idempotency_key=_idempotency_key, request_fingerprint=fingerprint,
            )
        except DurableDuplicateExecution as exc:
            raise DurableProviderDuplicate(exc.execution_id, exc.status) from exc
        except DurableIdempotencyConflict as exc:
            raise DurableProviderAdmissionRejected("idempotency_key_conflict") from exc
        except (DurableBudgetExceeded, DurableCircuitOpen) as exc:
            raise DurableProviderAdmissionRejected(str(exc)) from exc

        try:
            provider = getattr(self.adapter, "provider", None)
            native_idempotency = getattr(provider, "idempotency_mode", None) == "native"
            adapter_request = dict(request)
            if native_idempotency and _idempotency_key is not None:
                adapter_request["idempotency_key"] = _idempotency_key
            response = self.adapter(**adapter_request)
        except ProviderCancelled:
            self.governance_store.record_outcome(
                lease.lease_id, outcome="cancelled", reason="provider_cancelled",
            )
            raise
        except Exception:
            self.governance_store.record_outcome(
                lease.lease_id, outcome="failed", reason="provider_exception",
            )
            raise
        if not self.governance_store.record_outcome(
            lease.lease_id, outcome="succeeded", reason="provider_success",
        ):
            raise DurableProviderLeaseLost("provider_lease_lost_before_success_commit")
        return response


__all__ = [
    "DurableGovernedProviderExecutor", "DurableProviderAdmissionRejected",
    "DurableProviderDuplicate", "DurableProviderLeaseLost",
]
