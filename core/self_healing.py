"""Explicit, bounded health checks and recovery actions for Yuki.

Recovery handlers must be registered deliberately. This module never deletes data,
restarts services, or executes arbitrary commands on its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import Callable


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class RecoveryStatus(str, Enum):
    NOT_NEEDED = "not_needed"
    RECOVERED = "recovered"
    DENIED = "denied"
    COOLDOWN = "cooldown"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"
    UNVERIFIED = "unverified"
    NO_HANDLER = "no_handler"


@dataclass(frozen=True)
class HealthResult:
    name: str
    status: HealthStatus
    message: str = ""
    checked_at: float = 0.0


@dataclass(frozen=True)
class RecoveryResult:
    name: str
    status: RecoveryStatus
    message: str = ""


@dataclass(frozen=True)
class _RecoveryPolicy:
    handler: Callable[[], object]
    verify: Callable[[], bool]
    cooldown: float
    max_recoveries: int
    approval_required: bool


class SelfHealingManager:
    """Runs registered health checks and approval-gated, budgeted recovery handlers."""

    def __init__(self, *, approval: Callable[[str, str], bool] | None = None):
        self._approval = approval
        self._checks: dict[str, Callable[[], HealthResult | bool]] = {}
        self._policies: dict[str, _RecoveryPolicy] = {}
        self._last_recovery: dict[str, float] = {}
        self._recovery_counts: dict[str, int] = {}
        self._lock = threading.RLock()

    def register_check(self, name: str, check: Callable[[], HealthResult | bool]) -> None:
        self._validate_name(name)
        with self._lock:
            if name in self._checks:
                raise ValueError(f"Health check already registered: {name}")
            self._checks[name] = check

    def register_recovery(self, name: str, handler: Callable[[], object], *,
                          verify: Callable[[], bool], cooldown: float = 60.0,
                          max_recoveries: int = 3, approval_required: bool = True) -> None:
        self._validate_name(name)
        if cooldown < 0:
            raise ValueError("cooldown must be >= 0")
        if isinstance(max_recoveries, bool) or max_recoveries < 1:
            raise ValueError("max_recoveries must be >= 1")
        with self._lock:
            if name in self._policies:
                raise ValueError(f"Recovery handler already registered: {name}")
            self._policies[name] = _RecoveryPolicy(handler, verify, float(cooldown),
                                                   max_recoveries, bool(approval_required))

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("name must be a non-empty string of at most 100 characters")

    def check(self, name: str) -> HealthResult:
        with self._lock:
            check = self._checks.get(name)
        if check is None:
            return HealthResult(name, HealthStatus.UNKNOWN, "No health check registered.", time.time())
        try:
            result = check()
            if isinstance(result, HealthResult):
                return HealthResult(name, result.status, result.message, time.time())
            if isinstance(result, bool):
                return HealthResult(name, HealthStatus.HEALTHY if result else HealthStatus.UNHEALTHY,
                                    "Check returned boolean.", time.time())
            return HealthResult(name, HealthStatus.UNKNOWN, "Check returned unsupported result.", time.time())
        except Exception:
            return HealthResult(name, HealthStatus.UNKNOWN, "Health check raised an exception.", time.time())

    def check_all(self) -> list[HealthResult]:
        with self._lock:
            names = list(self._checks)
        return [self.check(name) for name in names]

    def recover(self, name: str, *, reason: str = "health check failed") -> RecoveryResult:
        with self._lock:
            policy = self._policies.get(name)
            if policy is None:
                return RecoveryResult(name, RecoveryStatus.NO_HANDLER, "No recovery handler registered.")
            now = time.monotonic()
            last = self._last_recovery.get(name)
            if last is not None and now - last < policy.cooldown:
                return RecoveryResult(name, RecoveryStatus.COOLDOWN, "Recovery cooldown is active.")
            count = self._recovery_counts.get(name, 0)
            if count >= policy.max_recoveries:
                return RecoveryResult(name, RecoveryStatus.BUDGET_EXHAUSTED, "Recovery budget exhausted.")
            # Reserve the attempt before approval to prevent concurrent overrun. A denial
            # releases the reservation, so declined actions do not consume the budget.
            self._recovery_counts[name] = count + 1
            self._last_recovery[name] = now

        if policy.approval_required:
            try:
                approved = self._approval is not None and bool(self._approval(name, reason))
            except Exception:
                approved = False
            if not approved:
                with self._lock:
                    self._recovery_counts[name] = max(0, self._recovery_counts.get(name, 1) - 1)
                    if self._recovery_counts[name] == 0:
                        self._recovery_counts.pop(name, None)
                    self._last_recovery.pop(name, None)
                return RecoveryResult(name, RecoveryStatus.DENIED, "Approval was not granted.")

        try:
            policy.handler()
        except Exception:
            return RecoveryResult(name, RecoveryStatus.FAILED, "Recovery handler failed.")
        try:
            verified = bool(policy.verify())
        except Exception:
            verified = False
        if not verified:
            return RecoveryResult(name, RecoveryStatus.UNVERIFIED, "Recovery ran but health was not restored.")
        return RecoveryResult(name, RecoveryStatus.RECOVERED, "Recovery verified successfully.")
