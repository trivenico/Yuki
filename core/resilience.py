"""Small standard-library resilience primitives for Yuki runtime services."""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

class CircuitOpenError(RuntimeError):
    """Raised when a circuit is open and a call is rejected before execution."""

class RetryExhausted(RuntimeError):
    """Raised after the retry policy is exhausted."""

@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.1
    max_delay: float = 2.0
    jitter: float = 0.1

    def __post_init__(self):
        if type(self.attempts) is not int or not 1 <= self.attempts <= 10:
            raise ValueError("attempts must be between 1 and 10")
        if self.base_delay < 0 or self.max_delay < self.base_delay:
            raise ValueError("delay bounds are invalid")
        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter must be between 0 and 1")

async def retry_async(operation: Callable[[], Awaitable[T]], *, policy: RetryPolicy = RetryPolicy(),
                      retry_on: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError),
                      sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> T:
    """Retry only declared transient exceptions; never swallow cancellation."""
    if not callable(operation):
        raise TypeError("operation must be callable")
    last_error = None
    for attempt in range(policy.attempts):
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except retry_on as exc:
            last_error = exc
            if attempt + 1 < policy.attempts:
                delay = min(policy.max_delay, policy.base_delay * (2 ** attempt))
                delay *= 1 + random.uniform(-policy.jitter, policy.jitter)
                await sleep(max(0, delay))
    raise RetryExhausted(f"Operation failed after {policy.attempts} attempts") from last_error

class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    """Process-local breaker; callers should create one per dependency/operation."""
    def __init__(self, *, failure_threshold: int = 5, reset_timeout: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        if type(failure_threshold) is not int or not 1 <= failure_threshold <= 100:
            raise ValueError("failure_threshold must be between 1 and 100")
        if reset_timeout <= 0:
            raise ValueError("reset_timeout must be positive")
        self.failure_threshold = failure_threshold
        self.reset_timeout = float(reset_timeout)
        self.clock = clock
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.opened_at = 0.0
        self._probe_in_flight = False

    def allow_call(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN and self.clock() - self.opened_at >= self.reset_timeout:
            self.state = CircuitState.HALF_OPEN
            self._probe_in_flight = False
        if self.state == CircuitState.HALF_OPEN and not self._probe_in_flight:
            self._probe_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.state = CircuitState.CLOSED
        self._probe_in_flight = False

    def record_failure(self) -> None:
        self.failures += 1
        self._probe_in_flight = False
        if self.state == CircuitState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = self.clock()

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        if not self.allow_call():
            raise CircuitOpenError("Dependency circuit is open.")
        try:
            result = await operation()
        except asyncio.CancelledError:
            self._probe_in_flight = False
            raise
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result
