"""Dependency-agnostic resilience primitives for the Yuki runtime.

These helpers report health and bound retries; they do not restart processes or
perform infrastructure mutations. Integrators must choose retryable exceptions
and ensure retried operations are idempotent.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class HealthCheck:
    name: str
    status: HealthStatus
    checked_at: float
    latency_ms: float
    detail: str = ""


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff with optional proportional jitter."""
    max_attempts: int = 3
    base_delay_seconds: float = 0.2
    max_delay_seconds: float = 3.0
    jitter_ratio: float = 0.1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays must be non-negative")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")


class RetryExhausted(RuntimeError):
    """Raised after a retryable operation fails on every allowed attempt."""

    def __init__(self, attempts: int, last_error: BaseException) -> None:
        super().__init__(f"operation failed after {attempts} attempts")
        self.attempts = attempts
        self.last_error = last_error


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy = RetryPolicy(),
    retry_on: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    random_value: Callable[[], float] = random.random,
) -> T:
    """Retry only explicitly retryable errors, preserving task cancellation."""
    last_error: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except retry_on as exc:
            last_error = exc
            if attempt == policy.max_attempts:
                break
            delay = min(
                policy.max_delay_seconds,
                policy.base_delay_seconds * (2 ** (attempt - 1)),
            )
            if policy.jitter_ratio:
                sample = min(1.0, max(0.0, random_value()))
                delay *= 1 - policy.jitter_ratio + 2 * policy.jitter_ratio * sample
            await sleep(max(0.0, delay))
    assert last_error is not None
    raise RetryExhausted(policy.max_attempts, last_error) from last_error


async def run_health_check(
    name: str,
    probe: Callable[[], Awaitable[object]],
    *,
    timeout_seconds: float = 2.0,
) -> HealthCheck:
    """Run a non-mutating async probe with timeout and sanitized error detail."""
    if not name or not name.strip():
        raise ValueError("name must be a non-empty string")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be > 0")
    started = time.monotonic()
    try:
        await asyncio.wait_for(probe(), timeout=timeout_seconds)
        status, detail = HealthStatus.HEALTHY, "probe succeeded"
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        status, detail = HealthStatus.UNHEALTHY, "probe timed out"
    except Exception as exc:
        # Raw exception messages may contain secrets, URLs, or user data.
        status, detail = HealthStatus.UNHEALTHY, type(exc).__name__
    return HealthCheck(
        name=name,
        status=status,
        checked_at=time.time(),
        latency_ms=round((time.monotonic() - started) * 1000, 2),
        detail=detail,
    )


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a dependency call is blocked by an open circuit."""


class CircuitBreaker:
    """Small circuit-breaker state machine.

    For use across concurrent tasks, guard every method with the same asyncio
    lock (or provide a concurrency-safe wrapper). A HALF_OPEN transition should
    permit only one probe at a time in that wrapper.
    """
    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 15.0):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.opened_at: float | None = None

    def allow_call(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if self.state is CircuitState.OPEN:
            if self.opened_at is not None and now - self.opened_at >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.state = CircuitState.CLOSED

    def record_failure(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.failures += 1
        if self.state is CircuitState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = now

    def require_call_allowed(self, now: float | None = None) -> None:
        if not self.allow_call(now):
            raise CircuitOpenError("dependency circuit is open")
