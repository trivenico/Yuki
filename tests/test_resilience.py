import asyncio
import unittest
from core.resilience import CircuitBreaker, CircuitOpenError, CircuitState, RetryExhausted, RetryPolicy, retry_async

class ResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_succeeds_after_transient_error(self):
        count = 0
        async def op():
            nonlocal count
            count += 1
            if count == 1: raise TimeoutError("transient")
            return "ok"
        result = await retry_async(op, policy=RetryPolicy(attempts=2, base_delay=0, max_delay=0), sleep=lambda _: asyncio.sleep(0))
        self.assertEqual(result, "ok")
        self.assertEqual(count, 2)

    async def test_retry_exhaustion_does_not_leak_original_message(self):
        async def op(): raise ConnectionError("sensitive endpoint detail")
        with self.assertRaises(RetryExhausted) as caught:
            await retry_async(op, policy=RetryPolicy(attempts=2, base_delay=0, max_delay=0), sleep=lambda _: asyncio.sleep(0))
        self.assertNotIn("sensitive endpoint", str(caught.exception))

    async def test_non_retryable_error_propagates(self):
        async def op(): raise ValueError("bad input")
        with self.assertRaises(ValueError):
            await retry_async(op, policy=RetryPolicy(attempts=3))

    async def test_circuit_opens_and_half_open_success_closes(self):
        now = [0.0]
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout=5, clock=lambda: now[0])
        async def fail(): raise TimeoutError()
        with self.assertRaises(TimeoutError): await breaker.call(fail)
        self.assertEqual(breaker.state, CircuitState.OPEN)
        with self.assertRaises(CircuitOpenError): await breaker.call(lambda: asyncio.sleep(0, result="no"))
        now[0] = 6
        self.assertEqual(await breaker.call(lambda: asyncio.sleep(0, result="yes")), "yes")
        self.assertEqual(breaker.state, CircuitState.CLOSED)

    async def test_half_open_allows_only_one_probe(self):
        now = [0.0]
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout=1, clock=lambda: now[0])
        breaker.record_failure()
        now[0] = 2
        self.assertTrue(breaker.allow_call())
        self.assertFalse(breaker.allow_call())

if __name__ == "__main__": unittest.main()
