"""Synchronous resilience primitives for bounded dependency calls.

These helpers do not create timeouts for blocking functions. Callers must configure
real timeouts in HTTP/LLM/database clients and should keep recovery outside retries.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import random
import threading
import time
from functools import wraps
from typing import Callable, TypeVar, ParamSpec

P = ParamSpec("P")
R = TypeVar("R")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a circuit rejects a call before invoking its dependency."""


@dataclass(frozen=True)
class CircuitSnapshot:
    name: str
    state: CircuitState
    failures: int
    opened_at: float | None
    half_open_in_flight: bool


class CircuitBreaker:
    """Thread-safe circuit breaker with one half-open probe at a time."""

    def __init__(self, name: str, *, failure_threshold: int = 5,
                 recovery_timeout: float = 30.0,
                 expected_exceptions: tuple[type[BaseException], ...] = (Exception,)):
        if not name.strip():
            raise ValueError("name must not be empty")
        if isinstance(failure_threshold, bool) or failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be > 0")
        if not expected_exceptions or not all(isinstance(e, type) and issubclass(e, BaseException) for e in expected_exceptions):
            raise ValueError("expected_exceptions must contain exception classes")
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = float(recovery_timeout)
        self.expected_exceptions = expected_exceptions
        self._lock = threading.RLock()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    def _before_call(self) -> bool:
        """Return True when this call owns the half-open probe."""
        with self._lock:
            if self._state is CircuitState.OPEN:
                if self._opened_at is None or time.monotonic() - self._opened_at < self.recovery_timeout:
                    raise CircuitOpenError(f"Circuit '{self.name}' is open")
                self._state = CircuitState.HALF_OPEN
            if self._state is CircuitState.HALF_OPEN:
                if self._probe_in_flight:
                    raise CircuitOpenError(f"Circuit '{self.name}' is waiting for its probe")
                self._probe_in_flight = True
                return True
            return False

    def call(self, function: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
        probe = self._before_call()
        try:
            result = function(*args, **kwargs)
        except self.expected_exceptions:
            with self._lock:
                self._failures += 1
                self._probe_in_flight = False
                if probe or self._failures >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._opened_at = time.monotonic()
            raise
        except BaseException:
            with self._lock:
                self._probe_in_flight = False
            raise
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED
            self._opened_at = None
            self._probe_in_flight = False
        return result

    def snapshot(self) -> CircuitSnapshot:
        with self._lock:
            return CircuitSnapshot(self.name, self._state, self._failures,
                                   self._opened_at, self._probe_in_flight)


def retry(*, attempts: int = 3, base_delay: float = 0.1, max_delay: float = 2.0,
          jitter: float = 0.1,
          retry_exceptions: tuple[type[BaseException], ...] = (Exception,)):
    """Retry a function with bounded exponential backoff and jitter.

    Use only for operations safe to retry; do not retry non-idempotent actions
    unless they have an idempotency key or equivalent protection.
    """
    if isinstance(attempts, bool) or attempts < 1:
        raise ValueError("attempts must be >= 1")
    if base_delay < 0 or max_delay < 0 or jitter < 0:
        raise ValueError("delays and jitter must be >= 0")
    if not retry_exceptions:
        raise ValueError("retry_exceptions must not be empty")

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            for attempt in range(attempts):
                try:
                    return function(*args, **kwargs)
                except retry_exceptions:
                    if attempt + 1 >= attempts:
                        raise
                    delay = min(max_delay, base_delay * (2 ** attempt))
                    if jitter:
                        delay += random.uniform(0.0, jitter * delay)
                    if delay:
                        time.sleep(delay)
            raise AssertionError("unreachable")
        return wrapped
    return decorate


class StateGuard:
    """Small lock-backed guard for compound state mutations."""

    def __init__(self):
        self._lock = threading.RLock()

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._lock.release()
        return False


def protected(breaker: CircuitBreaker):
    """Decorator routing a synchronous call through a circuit breaker."""
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            return breaker.call(function, *args, **kwargs)
        return wrapped
    return decorate
