"""Thread-safe provider circuit breaker state.

This component records outcomes supplied by the caller. It performs no network
requests, retries, provider selection, or fallback. Use monotonic time so wall
clock adjustments cannot extend or shorten an open interval.
"""
from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,47}$")


@dataclass
class _Circuit:
    failures: int = 0
    opened_until: float | None = None
    probe_in_flight: bool = False


@dataclass(frozen=True)
class CircuitSnapshot:
    provider_id: str
    state: str
    consecutive_failures: int
    retry_after_seconds: float


class ProviderCircuitBreaker:
    """Closed -> open -> half-open -> closed, with one half-open probe."""

    def __init__(
        self, *, failure_threshold: int = 5, reset_timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(failure_threshold) is not int or not 1 <= failure_threshold <= 100:
            raise ValueError("failure_threshold_must_be_1_to_100")
        if (isinstance(reset_timeout_seconds, bool)
                or not isinstance(reset_timeout_seconds, (int, float))
                or not math.isfinite(reset_timeout_seconds)
                or not 0.1 <= reset_timeout_seconds <= 3600):
            raise ValueError("reset_timeout_seconds_out_of_range")
        if not callable(clock):
            raise TypeError("clock_must_be_callable")
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = float(reset_timeout_seconds)
        self._clock = clock
        self._lock = threading.RLock()
        self._circuits: dict[str, _Circuit] = {}

    @staticmethod
    def _validate_provider_id(provider_id: str) -> None:
        if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
            raise ValueError("provider_id_invalid")

    def allow_request(self, provider_id: str) -> bool:
        """Reserve one request; in half-open state only one probe is admitted."""
        self._validate_provider_id(provider_id)
        now = self._clock()
        with self._lock:
            circuit = self._circuits.setdefault(provider_id, _Circuit())
            if circuit.opened_until is None:
                return True
            if now < circuit.opened_until:
                return False
            if circuit.probe_in_flight:
                return False
            circuit.probe_in_flight = True
            return True

    def record_success(self, provider_id: str) -> None:
        self._validate_provider_id(provider_id)
        with self._lock:
            self._circuits[provider_id] = _Circuit()

    def record_failure(self, provider_id: str) -> None:
        self._validate_provider_id(provider_id)
        now = self._clock()
        with self._lock:
            circuit = self._circuits.setdefault(provider_id, _Circuit())
            circuit.failures += 1
            circuit.probe_in_flight = False
            if circuit.opened_until is not None or circuit.failures >= self.failure_threshold:
                circuit.opened_until = now + self.reset_timeout_seconds

    def release_probe(self, provider_id: str) -> None:
        """Release a half-open reservation when execution was cancelled before a result."""
        self._validate_provider_id(provider_id)
        with self._lock:
            circuit = self._circuits.get(provider_id)
            if circuit is not None and circuit.opened_until is not None:
                circuit.probe_in_flight = False

    def snapshot(self, provider_id: str) -> CircuitSnapshot:
        self._validate_provider_id(provider_id)
        now = self._clock()
        with self._lock:
            circuit = self._circuits.get(provider_id, _Circuit())
            if circuit.opened_until is None:
                state = "closed"
                retry_after = 0.0
            elif now < circuit.opened_until or circuit.probe_in_flight:
                state = "open"
                retry_after = max(0.0, circuit.opened_until - now)
            else:
                state = "half_open"
                retry_after = 0.0
            return CircuitSnapshot(
                provider_id=provider_id,
                state=state,
                consecutive_failures=circuit.failures,
                retry_after_seconds=retry_after,
            )


__all__ = ["CircuitSnapshot", "ProviderCircuitBreaker"]
