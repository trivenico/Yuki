import asyncio
import unittest

from core.resilience import (
    CircuitBreaker, CircuitOpenError, CircuitState, HealthStatus, RetryExhausted,
    RetryPolicy, retry_async, run_health_check,
)


class ResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_succeeds_after_transient_failure(self):
        calls = 0

        async def operation():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ConnectionError("temporary")
            return "ready"

        async def no_sleep(_delay):
            return None

        result = await retry_async(
            operation,
            policy=RetryPolicy(max_attempts=3, jitter_ratio=0),
            sleep=no_sleep,
        )
        self.assertEqual(result, "ready")
        self.assertEqual(calls, 3)

    async def test_retry_stops_at_configured_limit(self):
        calls = 0

        async def operation():
            nonlocal calls
            calls += 1
            raise TimeoutError("not ready")

        async def no_sleep(_delay):
            return None

        with self.assertRaises(RetryExhausted) as raised:
            await retry_async(
                operation,
                policy=RetryPolicy(max_attempts=2, jitter_ratio=0),
                sleep=no_sleep,
            )
        self.assertEqual(raised.exception.attempts, 2)
        self.assertEqual(calls, 2)

    async def test_non_retryable_error_is_not_retried(self):
        calls = 0

        async def operation():
            nonlocal calls
            calls += 1
            raise ValueError("invalid request")

        with self.assertRaises(ValueError):
            await retry_async(operation)
        self.assertEqual(calls, 1)

    async def test_cancellation_is_not_swallowed(self):
        async def operation():
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await retry_async(operation)

    async def test_health_probe_success(self):
        async def probe():
            return True

        result = await run_health_check("db", probe)
        self.assertEqual(result.status, HealthStatus.HEALTHY)
        self.assertEqual(result.detail, "probe succeeded")

    async def test_health_probe_failure_does_not_leak_exception_message(self):
        async def probe():
            raise RuntimeError("token=private-secret")

        result = await run_health_check("db", probe)
        self.assertEqual(result.status, HealthStatus.UNHEALTHY)
        self.assertEqual(result.detail, "RuntimeError")
        self.assertNotIn("private-secret", result.detail)

    async def test_health_probe_timeout(self):
        async def probe():
            await asyncio.sleep(1)

        result = await run_health_check("slow", probe, timeout_seconds=0.01)
        self.assertEqual(result.status, HealthStatus.UNHEALTHY)
        self.assertEqual(result.detail, "probe timed out")

    async def test_invalid_health_check_timeout_rejected(self):
        async def probe():
            return True

        with self.assertRaises(ValueError):
            await run_health_check("db", probe, timeout_seconds=0)


class CircuitBreakerTests(unittest.TestCase):
    def test_opens_then_allows_probe_after_cooldown(self):
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=5)
        breaker.record_failure(now=10)
        breaker.record_failure(now=11)
        self.assertEqual(breaker.state, CircuitState.OPEN)
        with self.assertRaises(CircuitOpenError):
            breaker.require_call_allowed(now=12)
        self.assertTrue(breaker.allow_call(now=16))
        self.assertEqual(breaker.state, CircuitState.HALF_OPEN)
        breaker.record_success()
        self.assertEqual(breaker.state, CircuitState.CLOSED)

    def test_half_open_failure_reopens_circuit(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=2)
        breaker.record_failure(now=10)
        self.assertTrue(breaker.allow_call(now=12))
        breaker.record_failure(now=12)
        self.assertEqual(breaker.state, CircuitState.OPEN)


if __name__ == "__main__":
    unittest.main()
