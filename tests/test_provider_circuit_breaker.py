"""Policy tests for provider circuit-breaker transitions."""
import threading
import unittest

from providers.provider_circuit_breaker import ProviderCircuitBreaker


class ProviderCircuitBreakerTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.breaker = ProviderCircuitBreaker(
            failure_threshold=2, reset_timeout_seconds=10,
            clock=lambda: self.now,
        )

    def test_stays_closed_below_failure_threshold(self):
        self.breaker.record_failure("ollama")
        self.assertEqual(self.breaker.snapshot("ollama").state, "closed")
        self.assertTrue(self.breaker.allow_request("ollama"))

    def test_opens_at_threshold_and_rejects_requests(self):
        self.breaker.record_failure("ollama")
        self.breaker.record_failure("ollama")
        snapshot = self.breaker.snapshot("ollama")
        self.assertEqual(snapshot.state, "open")
        self.assertEqual(snapshot.retry_after_seconds, 10)
        self.assertFalse(self.breaker.allow_request("ollama"))

    def test_half_open_admits_only_one_probe(self):
        self.breaker.record_failure("ollama")
        self.breaker.record_failure("ollama")
        self.now += 10
        self.assertEqual(self.breaker.snapshot("ollama").state, "half_open")
        self.assertTrue(self.breaker.allow_request("ollama"))
        self.assertFalse(self.breaker.allow_request("ollama"))

    def test_probe_success_closes_and_resets_failures(self):
        self.breaker.record_failure("ollama")
        self.breaker.record_failure("ollama")
        self.now += 10
        self.assertTrue(self.breaker.allow_request("ollama"))
        self.breaker.record_success("ollama")
        snapshot = self.breaker.snapshot("ollama")
        self.assertEqual(snapshot.state, "closed")
        self.assertEqual(snapshot.consecutive_failures, 0)

    def test_stale_success_cannot_close_open_circuit(self):
        self.breaker.record_failure("ollama")
        self.breaker.record_failure("ollama")
        self.breaker.record_success("ollama")
        self.assertEqual(self.breaker.snapshot("ollama").state, "open")
        self.assertFalse(self.breaker.allow_request("ollama"))

    def test_probe_failure_reopens_circuit(self):
        self.breaker.record_failure("ollama")
        self.breaker.record_failure("ollama")
        self.now += 10
        self.assertTrue(self.breaker.allow_request("ollama"))
        self.breaker.record_failure("ollama")
        self.assertEqual(self.breaker.snapshot("ollama").state, "open")
        self.assertFalse(self.breaker.allow_request("ollama"))

    def test_cancelled_probe_can_be_released(self):
        self.breaker.record_failure("ollama")
        self.breaker.record_failure("ollama")
        self.now += 10
        self.assertTrue(self.breaker.allow_request("ollama"))
        self.breaker.release_probe("ollama")
        self.assertTrue(self.breaker.allow_request("ollama"))

    def test_provider_states_are_isolated(self):
        self.breaker.record_failure("ollama")
        self.breaker.record_failure("ollama")
        self.assertTrue(self.breaker.allow_request("gemini"))
        self.assertFalse(self.breaker.allow_request("ollama"))

    def test_concurrent_half_open_reservations_admit_exactly_one(self):
        self.breaker.record_failure("ollama")
        self.breaker.record_failure("ollama")
        self.now += 10
        barrier = threading.Barrier(8)
        results = []
        lock = threading.Lock()

        def attempt():
            barrier.wait()
            result = self.breaker.allow_request("ollama")
            with lock:
                results.append(result)

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(results), 1)

    def test_invalid_configuration_and_provider_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            ProviderCircuitBreaker(failure_threshold=0)
        with self.assertRaises(ValueError):
            self.breaker.allow_request("Invalid ID")


if __name__ == "__main__":
    unittest.main()
