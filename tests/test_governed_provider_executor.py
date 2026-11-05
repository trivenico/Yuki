"""Tests for unified provider dispatch admission and cleanup."""
import unittest

from providers.agent_adapter import ProviderAgentAdapter
from providers.provider_circuit_breaker import ProviderCircuitBreaker
from providers.provider_execution import ProviderCancelled
from providers.provider_gateway import ProviderGateway
from providers.provider_request_budget import ProviderBudgetExceeded, ProviderRequestBudget
from providers.governed_provider_executor import GovernedProviderExecutor, ProviderCircuitOpen


class ReliableLocalModel:
    privacy_class = "local"

    def __init__(self, *, fail=False, cancel=False):
        self.fail = fail
        self.cancel = cancel

    def generate_chat(self, *, messages, tools, tool_choice):
        if self.cancel:
            raise ProviderCancelled("cancelled")
        if self.fail:
            raise RuntimeError("provider transport failed")
        return {"text": "ready", "tool_calls": {"tool_calls": []}}


class GovernedProviderExecutorTests(unittest.TestCase):
    def setUp(self):
        self.breaker = ProviderCircuitBreaker(failure_threshold=1, reset_timeout_seconds=10)
        self.budget = ProviderRequestBudget(
            max_global_concurrent=2, max_provider_concurrent=2,
            max_agent_concurrent=1, max_agent_requests_per_window=10,
        )
        self.request = {
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [], "tool_choice": "none",
        }

    def build(self, model):
        gateway = ProviderGateway.from_adapter(ProviderAgentAdapter(model))
        return GovernedProviderExecutor(
            provider_id=gateway.provider_id, agent_id="assistant",
            adapter=gateway, circuit_breaker=self.breaker,
            request_budget=self.budget,
        )

    def test_success_records_outcome_and_releases_capacity(self):
        execute = self.build(ReliableLocalModel())
        result = execute(**self.request)
        self.assertEqual(result["text"], "ready")
        snapshot = self.budget.snapshot(provider_id="reliablelocal", agent_id="assistant")
        self.assertEqual(snapshot.global_active_requests, 0)
        self.assertEqual(self.breaker.snapshot("reliablelocal").state, "closed")

    def test_provider_exception_opens_circuit_and_releases_capacity(self):
        execute = self.build(ReliableLocalModel(fail=True))
        with self.assertRaisesRegex(RuntimeError, "transport"):
            execute(**self.request)
        self.assertEqual(self.breaker.snapshot("reliablelocal").state, "open")
        self.assertEqual(
            self.budget.snapshot(provider_id="reliablelocal", agent_id="assistant").global_active_requests,
            0,
        )
        with self.assertRaises(ProviderCircuitOpen):
            execute(**self.request)

    def test_cancellation_does_not_mark_provider_unhealthy(self):
        execute = self.build(ReliableLocalModel(cancel=True))
        with self.assertRaises(ProviderCancelled):
            execute(**self.request)
        self.assertEqual(self.breaker.snapshot("reliablelocal").state, "closed")
        self.assertEqual(
            self.budget.snapshot(provider_id="reliablelocal", agent_id="assistant").global_active_requests,
            0,
        )

    def test_budget_rejection_releases_half_open_probe(self):
        execute = self.build(ReliableLocalModel())
        self.breaker.record_failure("reliablelocal")
        # Use a clock-controlled circuit to make its probe available.
        now = [0.0]
        breaker = ProviderCircuitBreaker(
            failure_threshold=1, reset_timeout_seconds=1, clock=lambda: now[0],
        )
        breaker.record_failure("reliablelocal")
        now[0] = 1.0
        occupied = self.budget.acquire(provider_id="reliablelocal", agent_id="other")
        constrained = GovernedProviderExecutor(
            provider_id="reliablelocal", agent_id="assistant",
            adapter=execute.adapter, circuit_breaker=breaker, request_budget=self.budget,
        )
        with self.assertRaises(ProviderBudgetExceeded):
            constrained(**self.request)
        self.assertTrue(breaker.allow_request("reliablelocal"))
        self.budget.release(occupied)

    def test_adapter_validation_error_is_released_and_recorded(self):
        execute = self.build(ReliableLocalModel())
        with self.assertRaises(ValueError):
            execute(messages=[], tools=[], tool_choice="none")
        self.assertEqual(
            self.budget.snapshot(provider_id="reliablelocal", agent_id="assistant").global_active_requests,
            0,
        )


if __name__ == "__main__":
    unittest.main()
