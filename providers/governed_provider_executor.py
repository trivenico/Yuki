"""Unified provider dispatch lifecycle: circuit admission, budgets and outcomes.

This wrapper is intentionally synchronous and process-local. It does not retry,
fallback, persist state, or claim to interrupt legacy blocking provider calls.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from providers.provider_circuit_breaker import ProviderCircuitBreaker
from providers.provider_execution import ProviderCancelled
from providers.provider_request_budget import (
    ProviderBudgetExceeded, ProviderRequestBudget,
)


class ProviderCircuitOpen(RuntimeError):
    """Provider circuit is open and dispatch was rejected."""


class GovernedProviderExecutor:
    """Wrap a normalized provider adapter with admission and outcome accounting."""

    def __init__(
        self, *, provider_id: str, agent_id: str, adapter: Callable[..., Mapping[str, Any]],
        circuit_breaker: ProviderCircuitBreaker, request_budget: ProviderRequestBudget,
    ) -> None:
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError("provider_id_required")
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id_required")
        if not callable(adapter):
            raise TypeError("provider_adapter_must_be_callable")
        if not isinstance(circuit_breaker, ProviderCircuitBreaker):
            raise TypeError("provider_circuit_breaker_required")
        if not isinstance(request_budget, ProviderRequestBudget):
            raise TypeError("provider_request_budget_required")
        self.provider_id = provider_id
        self.agent_id = agent_id
        self.adapter = adapter
        self.circuit_breaker = circuit_breaker
        self.request_budget = request_budget

    def __call__(self, **request: Any) -> Mapping[str, Any]:
        if not self.circuit_breaker.allow_request(self.provider_id):
            raise ProviderCircuitOpen("provider_circuit_open")
        try:
            lease_id = self.request_budget.acquire(
                provider_id=self.provider_id, agent_id=self.agent_id,
            )
        except ProviderBudgetExceeded:
            # A half-open probe was reserved before budget admission. Return it
            # so a later request can use the probe slot.
            self.circuit_breaker.release_probe(self.provider_id)
            raise

        try:
            response = self.adapter(**request)
        except ProviderCancelled:
            # Cancellation is not evidence of provider unhealthiness.
            self.circuit_breaker.release_probe(self.provider_id)
            raise
        except Exception:
            self.circuit_breaker.record_failure(self.provider_id)
            raise
        else:
            self.circuit_breaker.record_success(self.provider_id)
            return response
        finally:
            self.request_budget.release(lease_id)


__all__ = ["GovernedProviderExecutor", "ProviderCircuitOpen"]
