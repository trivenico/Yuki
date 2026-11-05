"""Durable governed provider dispatch backed by shared SQLite reservations."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from providers.durable_provider_governance import (
    DurableBudgetExceeded, DurableCircuitOpen, SQLiteProviderGovernanceStore,
)
from providers.provider_execution import ProviderCancelled


class DurableProviderAdmissionRejected(RuntimeError):
    """Durable provider admission denied execution."""


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

    def __call__(self, **request: Any) -> Mapping[str, Any]:
        validate = getattr(self.adapter, "validate_inputs", None)
        if callable(validate):
            validate(
                request.get("messages"), request.get("tools"),
                request.get("tool_choice", "auto"),
            )
        try:
            lease = self.governance_store.acquire(
                provider_id=self.provider_id, agent_id=self.agent_id,
            )
        except (DurableBudgetExceeded, DurableCircuitOpen) as exc:
            raise DurableProviderAdmissionRejected(str(exc)) from exc

        try:
            response = self.adapter(**request)
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
    "DurableProviderLeaseLost",
]
