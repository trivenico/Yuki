"""Tests for bounded provider admission and request budgets."""
import threading
import unittest

from providers.provider_request_budget import (
    ProviderBudgetExceeded, ProviderRequestBudget,
)


class ProviderRequestBudgetTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.budget = ProviderRequestBudget(
            max_global_concurrent=3, max_provider_concurrent=2,
            max_agent_concurrent=1, max_agent_requests_per_window=2,
            window_seconds=10, lease_timeout_seconds=5,
            clock=lambda: self.now,
        )

    def test_reserves_and_releases_capacity_idempotently(self):
        lease = self.budget.acquire(provider_id="ollama", agent_id="planner")
        self.assertEqual(self.budget.snapshot(provider_id="ollama", agent_id="planner").active_requests, 1)
        self.assertTrue(self.budget.release(lease))
        self.assertFalse(self.budget.release(lease))

    def test_enforces_agent_concurrency(self):
        self.budget.acquire(provider_id="ollama", agent_id="planner")
        with self.assertRaisesRegex(ProviderBudgetExceeded, "agent_concurrency"):
            self.budget.acquire(provider_id="gemini", agent_id="planner")

    def test_enforces_provider_and_global_concurrency(self):
        self.budget.acquire(provider_id="ollama", agent_id="a")
        self.budget.acquire(provider_id="ollama", agent_id="b")
        with self.assertRaisesRegex(ProviderBudgetExceeded, "provider_concurrency"):
            self.budget.acquire(provider_id="ollama", agent_id="c")
        self.budget.acquire(provider_id="gemini", agent_id="c")
        with self.assertRaisesRegex(ProviderBudgetExceeded, "global_concurrency"):
            self.budget.acquire(provider_id="anthropic", agent_id="d")

    def test_enforces_per_agent_request_window_even_after_release(self):
        first = self.budget.acquire(provider_id="ollama", agent_id="planner")
        self.budget.release(first)
        second = self.budget.acquire(provider_id="ollama", agent_id="planner")
        self.budget.release(second)
        with self.assertRaisesRegex(ProviderBudgetExceeded, "request_rate"):
            self.budget.acquire(provider_id="ollama", agent_id="planner")
        self.now += 10
        lease = self.budget.acquire(provider_id="ollama", agent_id="planner")
        self.assertTrue(self.budget.release(lease))

    def test_expired_lease_is_reclaimed(self):
        self.budget.acquire(provider_id="ollama", agent_id="planner")
        self.now += 5
        lease = self.budget.acquire(provider_id="ollama", agent_id="planner")
        self.assertTrue(self.budget.release(lease))

    def test_concurrent_global_admission_is_bounded(self):
        budget = ProviderRequestBudget(max_global_concurrent=2,
            max_provider_concurrent=2, max_agent_concurrent=2,
            max_agent_requests_per_window=10)
        barrier = threading.Barrier(8)
        leases = []
        rejected = []
        lock = threading.Lock()
        def attempt(i):
            barrier.wait()
            try:
                value = budget.acquire(provider_id="ollama", agent_id="agent-"+str(i))
                with lock:
                    leases.append(value)
            except ProviderBudgetExceeded:
                with lock:
                    rejected.append(i)
        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(leases), 2)
        self.assertEqual(len(rejected), 6)

    def test_invalid_limits_and_identifiers_are_rejected(self):
        with self.assertRaises(ValueError):
            ProviderRequestBudget(max_global_concurrent=0)
        with self.assertRaises(ValueError):
            self.budget.acquire(provider_id="bad provider", agent_id="planner")


if __name__ == "__main__":
    unittest.main()
