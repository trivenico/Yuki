"""Small, dependency-free health-check primitives for Yuki.

Checks are explicitly registered by trusted application code. Running a check does
not restart services, mutate configuration, or execute recovery actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from time import monotonic
from typing import Callable, Iterable


@dataclass(frozen=True)
class HealthResult:
    name: str
    status: str
    detail: str
    duration_ms: int


@dataclass(frozen=True)
class HealthReport:
    status: str
    checked_at: str
    checks: tuple[HealthResult, ...]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "checked_at": self.checked_at,
            "checks": [
                {"name": item.name, "status": item.status,
                 "detail": item.detail, "duration_ms": item.duration_ms}
                for item in self.checks
            ],
        }


class ResilienceMonitor:
    """Run a bounded set of registered health checks and report their outcomes."""

    MAX_CHECKS = 100
    MAX_NAME = 80
    MAX_DETAIL = 300

    def __init__(self, checks: Iterable[tuple[str, Callable[[], object]]] = ()):
        self._checks: dict[str, Callable[[], object]] = {}
        self._lock = RLock()
        for name, check in checks:
            self.register(name, check)

    def register(self, name: str, check: Callable[[], object]) -> None:
        if not isinstance(name, str) or not name.strip() or len(name) > self.MAX_NAME:
            raise ValueError("Check name must be a non-empty string of at most 80 characters.")
        if not callable(check):
            raise TypeError("Health check must be callable.")
        with self._lock:
            if name in self._checks:
                raise ValueError("Health check name is already registered.")
            if len(self._checks) >= self.MAX_CHECKS:
                raise ValueError("Health check limit reached.")
            self._checks[name] = check

    def run(self) -> HealthReport:
        with self._lock:
            checks = tuple(self._checks.items())
        results = []
        for name, check in checks:
            started = monotonic()
            try:
                value = check()
                ok = value is True or (isinstance(value, dict) and value.get("ok") is True)
                detail = "check passed" if ok else "check reported unhealthy"
                status = "healthy" if ok else "unhealthy"
            except Exception as exc:
                # Do not leak exception messages, which may contain secrets or paths.
                status, detail = "error", f"check raised {type(exc).__name__}"
            elapsed = max(0, int((monotonic() - started) * 1000))
            results.append(HealthResult(name, status, detail[:self.MAX_DETAIL], elapsed))
        states = {item.status for item in results}
        overall = "healthy" if states <= {"healthy"} else ("error" if "error" in states else "unhealthy")
        return HealthReport(overall, datetime.now(timezone.utc).isoformat(), tuple(results))
