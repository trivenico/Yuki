"""Deterministic tests for bounded retries and provider circuit breaking."""

import threading
import unittest

from core.resilience import CircuitBreaker, CircuitOpenError, RetryPolicy, call_with_retry


class ResilienceTests(unittest.TestCase):
    def test_retry_requires_explicit_retry_safety(self):
        with self.assertRaisesRegex(ValueError, "retry_safe"):
            call_with_retry(lambda: None, policy=RetryPolicy(max_attempts=2))

    def test_retry_uses_bounded_exponential_backoff(self):
        calls = []
        delays = []

        def operation():
            calls.append(len(calls) + 1)
            if len(calls) < 3:
                raise TimeoutError("temporary")
            return "ok"

        result = call_with_retry(
            operation,
            policy=RetryPolicy(max_attempts=3, initial_delay_seconds=0.1, max_delay_seconds=0.2),
            retry_safe=True,
            sleep=delays.append,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(delays, [0.1, 0.2])

    def test_retry_does_not_retry_unlisted_errors(self):
        calls = []
        with self.assertRaisesRegex(ValueError, "invalid"):
            call_with_retry(
                lambda: (calls.append(1), (_ for _ in ()).throw(ValueError("invalid")))[1],
                policy=RetryPolicy(max_attempts=3),
                retry_safe=True,
            )
        self.assertEqual(len(calls), 1)

    def test_retry_policy_rejects_invalid_bounds(self):
        for kwargs in (
            {"max_attempts": 0},
            {"max_attempts": 6},
            {"initial_delay_seconds": -1},
            {"initial_delay_seconds": 2, "max_delay_seconds": 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RetryPolicy(**kwargs)

    def test_circuit_opens_after_threshold_and_recovers_with_probe(self):
        now = [100.0]
        breaker = CircuitBreaker(
            failure_threshold=2, recovery_timeout_seconds=5, clock=lambda: now[0]
        )

        def fail():
            raise ConnectionError("unavailable")

        for _ in range(2):
            with self.assertRaises(ConnectionError):
                breaker.call(fail)
        self.assertEqual(breaker.state, "open")
        with self.assertRaises(CircuitOpenError):
            breaker.call(lambda: "must not run")
        now[0] += 5
        self.assertEqual(breaker.state, "half_open")
        self.assertEqual(breaker.call(lambda: "healthy"), "healthy")
        self.assertEqual(breaker.state, "closed")

    def test_failed_recovery_probe_reopens_circuit(self):
        now = [10.0]
        breaker = CircuitBreaker(
            failure_threshold=1, recovery_timeout_seconds=2, clock=lambda: now[0]
        )
        with self.assertRaises(ConnectionError):
            breaker.call(lambda: (_ for _ in ()).throw(ConnectionError()))
        now[0] += 2
        with self.assertRaises(ConnectionError):
            breaker.call(lambda: (_ for _ in ()).throw(ConnectionError()))
        self.assertEqual(breaker.state, "open")

    def test_only_one_half_open_probe_runs_at_a_time(self):
        now = [0.0]
        breaker = CircuitBreaker(
            failure_threshold=1, recovery_timeout_seconds=1, clock=lambda: now[0]
        )
        with self.assertRaises(ConnectionError):
            breaker.call(lambda: (_ for _ in ()).throw(ConnectionError()))
        now[0] = 1.0
        entered = threading.Event()
        release = threading.Event()
        results = []

        def probe():
            entered.set()
            release.wait(timeout=2)
            return "ok"

        worker = threading.Thread(target=lambda: results.append(breaker.call(probe)))
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        with self.assertRaises(CircuitOpenError):
            breaker.call(lambda: "second probe")
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, ["ok"])
        self.assertEqual(breaker.state, "closed")

    def test_stale_success_cannot_close_circuit_opened_by_another_call(self):
        now = [0.0]
        breaker = CircuitBreaker(
            failure_threshold=1, recovery_timeout_seconds=10, clock=lambda: now[0]
        )
        entered = threading.Event()
        release = threading.Event()
        stale_result = []

        def slow_success():
            entered.set()
            release.wait(timeout=2)
            return "stale success"

        worker = threading.Thread(target=lambda: stale_result.append(breaker.call(slow_success)))
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        with self.assertRaises(ConnectionError):
            breaker.call(lambda: (_ for _ in ()).throw(ConnectionError("failure")))
        self.assertEqual(breaker.state, "open")
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(stale_result, ["stale success"])
        self.assertEqual(breaker.state, "open")

    def test_keyboard_interrupt_releases_half_open_probe(self):
        now = [0.0]
        breaker = CircuitBreaker(
            failure_threshold=1, recovery_timeout_seconds=1, clock=lambda: now[0]
        )
        with self.assertRaises(ConnectionError):
            breaker.call(lambda: (_ for _ in ()).throw(ConnectionError()))
        now[0] = 1.0
        with self.assertRaises(KeyboardInterrupt):
            breaker.call(lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
        self.assertEqual(breaker.call(lambda: "recovered"), "recovered")
        self.assertEqual(breaker.state, "closed")

    def test_circuit_configuration_is_validated(self):
        for kwargs in (
            {"failure_threshold": 0},
            {"failure_threshold": True},
            {"recovery_timeout_seconds": 0},
            {"recovery_timeout_seconds": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CircuitBreaker(**kwargs)


if __name__ == "__main__":
    unittest.main()
