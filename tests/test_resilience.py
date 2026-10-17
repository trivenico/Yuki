import unittest
from unittest.mock import patch

from core.resilience import CircuitBreaker, CircuitOpenError, CircuitState, retry


class ResilienceTests(unittest.TestCase):
    def test_opens_after_failure_threshold(self):
        breaker = CircuitBreaker("llm", failure_threshold=2, recovery_timeout=30)
        def fail():
            raise TimeoutError("timeout")
        for _ in range(2):
            with self.assertRaises(TimeoutError):
                breaker.call(fail)
        self.assertEqual(breaker.snapshot().state, CircuitState.OPEN)
        with self.assertRaises(CircuitOpenError):
            breaker.call(lambda: "not called")

    def test_half_open_success_closes_circuit(self):
        breaker = CircuitBreaker("db", failure_threshold=1, recovery_timeout=0.01)
        with self.assertRaises(ValueError):
            breaker.call(lambda: (_ for _ in ()).throw(ValueError()))
        import time
        time.sleep(0.02)
        self.assertEqual(breaker.call(lambda: "ok"), "ok")
        self.assertEqual(breaker.snapshot().state, CircuitState.CLOSED)

    def test_retry_is_bounded(self):
        calls = []
        @retry(attempts=3, base_delay=0, jitter=0)
        def flaky():
            calls.append(1)
            raise OSError("no")
        with patch("core.resilience.time.sleep"):
            with self.assertRaises(OSError):
                flaky()
        self.assertEqual(len(calls), 3)

    def test_retry_eventually_succeeds(self):
        calls = []
        @retry(attempts=3, base_delay=0, jitter=0)
        def flaky():
            calls.append(1)
            if len(calls) < 2:
                raise OSError("retry")
            return 42
        self.assertEqual(flaky(), 42)


if __name__ == "__main__":
    unittest.main()
