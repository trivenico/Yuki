"""Bounded, thread-safe provider request admission and budget reservations.

The budget is process-local. It limits concurrent work and per-agent request
volume, but does not estimate tokens, meter billing, or coordinate across hosts.
"""
from __future__ import annotations

import math
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_PROVIDER = re.compile(r"^[a-z][a-z0-9_.-]{0,47}$")


class ProviderBudgetExceeded(RuntimeError):
    """A provider request could not be admitted within configured budgets."""


@dataclass(frozen=True)
class ProviderBudgetSnapshot:
    provider_id: str
    active_requests: int
    global_active_requests: int
    agent_active_requests: int
    agent_requests_in_window: int
    agent_request_limit: int


@dataclass
class _Lease:
    provider_id: str
    agent_id: str
    expires_at: float


class ProviderRequestBudget:
    """Reserve bounded provider capacity before dispatch; release exactly once."""

    def __init__(
        self, *,
        max_global_concurrent: int = 32,
        max_provider_concurrent: int = 8,
        max_agent_concurrent: int = 2,
        max_agent_requests_per_window: int = 60,
        window_seconds: float = 60.0,
        lease_timeout_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for name, value, upper in (
            ("max_global_concurrent", max_global_concurrent, 100_000),
            ("max_provider_concurrent", max_provider_concurrent, 10_000),
            ("max_agent_concurrent", max_agent_concurrent, 10_000),
            ("max_agent_requests_per_window", max_agent_requests_per_window, 1_000_000),
        ):
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError(name + "_out_of_range")
        for name, value, upper in (
            ("window_seconds", window_seconds, 86_400.0),
            ("lease_timeout_seconds", lease_timeout_seconds, 86_400.0),
        ):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0.1 <= value <= upper):
                raise ValueError(name + "_out_of_range")
        if not callable(clock):
            raise TypeError("clock_must_be_callable")
        self.max_global_concurrent = max_global_concurrent
        self.max_provider_concurrent = max_provider_concurrent
        self.max_agent_concurrent = max_agent_concurrent
        self.max_agent_requests_per_window = max_agent_requests_per_window
        self.window_seconds = float(window_seconds)
        self.lease_timeout_seconds = float(lease_timeout_seconds)
        self._clock = clock
        self._lock = threading.RLock()
        self._leases: dict[str, _Lease] = {}
        self._agent_windows: dict[str, tuple[float, int]] = {}

    @staticmethod
    def _validate_ids(provider_id: str, agent_id: str) -> None:
        if not isinstance(provider_id, str) or not _PROVIDER.fullmatch(provider_id):
            raise ValueError("provider_id_invalid")
        if not isinstance(agent_id, str) or not _ID.fullmatch(agent_id):
            raise ValueError("agent_id_invalid")

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, lease in self._leases.items() if lease.expires_at <= now]
        for key in expired:
            del self._leases[key]

    def acquire(self, *, provider_id: str, agent_id: str) -> str:
        """Return an opaque lease ID or raise ProviderBudgetExceeded."""
        self._validate_ids(provider_id, agent_id)
        now = self._clock()
        with self._lock:
            self._purge_expired(now)
            active = list(self._leases.values())
            provider_count = sum(x.provider_id == provider_id for x in active)
            agent_count = sum(x.agent_id == agent_id for x in active)
            if len(active) >= self.max_global_concurrent:
                raise ProviderBudgetExceeded("global_concurrency_limit")
            if provider_count >= self.max_provider_concurrent:
                raise ProviderBudgetExceeded("provider_concurrency_limit")
            if agent_count >= self.max_agent_concurrent:
                raise ProviderBudgetExceeded("agent_concurrency_limit")

            window = self._agent_windows.get(agent_id)
            if window is None or now - window[0] >= self.window_seconds:
                window = (now, 0)
            if window[1] >= self.max_agent_requests_per_window:
                raise ProviderBudgetExceeded("agent_request_rate_limit")
            self._agent_windows[agent_id] = (window[0], window[1] + 1)

            lease_id = uuid.uuid4().hex
            self._leases[lease_id] = _Lease(
                provider_id=provider_id, agent_id=agent_id,
                expires_at=now + self.lease_timeout_seconds,
            )
            return lease_id

    def release(self, lease_id: str) -> bool:
        """Release an active reservation; repeated or expired releases are harmless."""
        if not isinstance(lease_id, str) or not re.fullmatch(r"[0-9a-f]{32}", lease_id):
            raise ValueError("lease_id_invalid")
        with self._lock:
            return self._leases.pop(lease_id, None) is not None

    def snapshot(self, *, provider_id: str, agent_id: str) -> ProviderBudgetSnapshot:
        self._validate_ids(provider_id, agent_id)
        now = self._clock()
        with self._lock:
            self._purge_expired(now)
            active = list(self._leases.values())
            window = self._agent_windows.get(agent_id)
            count = 0 if window is None or now - window[0] >= self.window_seconds else window[1]
            return ProviderBudgetSnapshot(
                provider_id=provider_id,
                active_requests=sum(x.provider_id == provider_id for x in active),
                global_active_requests=len(active),
                agent_active_requests=sum(x.agent_id == agent_id for x in active),
                agent_requests_in_window=count,
                agent_request_limit=self.max_agent_requests_per_window,
            )


__all__ = [
    "ProviderBudgetExceeded", "ProviderBudgetSnapshot", "ProviderRequestBudget",
]
