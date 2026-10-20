"""Bounded synchronous resilience primitives for provider and worker calls.

Retries are disabled unless the caller explicitly marks an operation retry-safe.
Hard cancellation of arbitrary Python callables is not promised; use
SubprocessExecutor for work that requires an enforceable process timeout.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable, TypeVar

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    """Raised when a circuit is open or a half-open probe is already running."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    initial_delay_seconds: float = 0.1
    max_delay_seconds: float = 1.0

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 5:
            raise ValueError("max_attempts must be an integer between 1 and 5")
        for name, value in (
            ("initial_delay_seconds", self.initial_delay_seconds),
            ("max_delay_seconds", self.max_delay_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")
        if self.initial_delay_seconds > self.max_delay_seconds:
            raise ValueError("initial delay cannot exceed maximum delay")


def call_with_retry(
    operation: Callable[[], T],
    *,
    policy: RetryPolicy = RetryPolicy(),
    retry_on: tuple[type[Exception], ...] = (TimeoutError, ConnectionError),
    retry_safe: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Retry selected transient errors with bounded exponential backoff.

    The caller must explicitly assert retry safety when more than one attempt is
    configured, preventing accidental replay of non-idempotent side effects.
    """
    if not callable(operation):
        raise TypeError("operation must be callable")
    if not isinstance(policy, RetryPolicy):
        raise TypeError("policy must be a RetryPolicy")
    if (
        not isinstance(retry_on, tuple)
        or not retry_on
        or any(not isinstance(error, type) or not issubclass(error, Exception) for error in retry_on)
    ):
        raise ValueError("retry_on must be a non-empty tuple of exception types")
    if not callable(sleep):
        raise TypeError("sleep must be callable")
    if policy.max_attempts > 1 and retry_safe is not True:
        raise ValueError("retries require explicit retry_safe=True")

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return operation()
        except retry_on:
            if attempt == policy.max_attempts:
                raise
            delay = min(
                policy.initial_delay_seconds * (2 ** (attempt - 1)),
                policy.max_delay_seconds,
            )
            if delay:
                sleep(delay)
    raise RuntimeError("unreachable retry state")


class CircuitBreaker:
    """Thread-safe circuit breaker with a single half-open probe."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(failure_threshold) is not int or not 1 <= failure_threshold <= 100:
            raise ValueError("failure_threshold must be an integer between 1 and 100")
        if (
            isinstance(recovery_timeout_seconds, bool)
            or not isinstance(recovery_timeout_seconds, (int, float))
            or not 0 < recovery_timeout_seconds <= 3600
        ):
            raise ValueError("recovery timeout must be between 0 and 3600 seconds")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = float(recovery_timeout_seconds)
        self._clock = clock
        self._lock = Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            if self._clock() - self._opened_at < self.recovery_timeout_seconds:
                return "open"
            return "half_open"

    def call(self, operation: Callable[..., T], *args, **kwargs) -> T:
        if not callable(operation):
            raise TypeError("operation must be callable")
        probe = False
        with self._lock:
            now = self._clock()
            if self._opened_at is not None:
                if now - self._opened_at < self.recovery_timeout_seconds:
                    raise CircuitOpenError("Circuit is open; operation was not attempted.")
                if self._probe_in_flight:
                    raise CircuitOpenError("Circuit recovery probe is already running.")
                self._probe_in_flight = True
                probe = True

        try:
            result = operation(*args, **kwargs)
        except Exception:
            with self._lock:
                self._failures += 1
                if probe or self._failures >= self.failure_threshold:
                    self._opened_at = self._clock()
                self._probe_in_flight = False
            raise
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False
        return result
