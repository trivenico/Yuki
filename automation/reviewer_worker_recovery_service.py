"""Supervised polling service for lease-aware reviewer recovery."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from typing import Callable

from automation.reviewer_worker_recovery import (
    ReviewerRecoveryReport, ReviewerWorkerRecoverySupervisor,
)


class ReviewerRecoveryServiceError(RuntimeError):
    """Recovery service lifecycle or configuration error."""


@dataclass(frozen=True)
class ReviewerRecoveryHeartbeat:
    state: str
    observed_at: str
    cycles: int
    recovered_tasks: int
    deferred_tasks: int
    consecutive_failures: int
    last_error_code: str | None


@dataclass(frozen=True)
class ReviewerRecoveryServiceReport:
    cycles: int
    recovered_tasks: int
    deferred_tasks: int
    failed_cycles: int
    stopped: bool
    last_report: ReviewerRecoveryReport | None


class ReviewerWorkerRecoveryService:
    """Single-threaded, bounded, restart-friendly recovery polling loop.

    The supervisor/ledger remain the recovery authority. This service only
    schedules bounded run_once calls and emits aggregate host-side health.
    """

    def __init__(
        self,
        supervisor: ReviewerWorkerRecoverySupervisor,
        *,
        heartbeat_callback: Callable[[ReviewerRecoveryHeartbeat], None],
        poll_interval_seconds: float = 10.0,
        max_backoff_seconds: float = 300.0,
        batch_limit: int = 100,
    ) -> None:
        if not isinstance(supervisor, ReviewerWorkerRecoverySupervisor):
            raise TypeError("reviewer_recovery_supervisor_required")
        if not callable(heartbeat_callback):
            raise TypeError("reviewer_recovery_heartbeat_callback_required")
        for value, name, low, high in (
            (poll_interval_seconds, "poll_interval", 0.1, 3600.0),
            (max_backoff_seconds, "max_backoff", 1.0, 86400.0),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError("invalid_reviewer_recovery_" + name)
        if type(batch_limit) is not int or not 1 <= batch_limit <= 500:
            raise ValueError("invalid_reviewer_recovery_batch_limit")
        self.supervisor = supervisor
        self.heartbeat_callback = heartbeat_callback
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.max_backoff_seconds = float(max_backoff_seconds)
        self.batch_limit = batch_limit
        self._stop = Event()
        self._running = False

    def stop(self) -> None:
        """Request shutdown after the current bounded recovery cycle."""
        self._stop.set()

    def _emit(self, state: str, cycles: int, recovered: int, deferred: int,
              failures: int, error: str | None) -> None:
        self.heartbeat_callback(ReviewerRecoveryHeartbeat(
            state=state, observed_at=datetime.now(timezone.utc).isoformat(),
            cycles=cycles, recovered_tasks=recovered, deferred_tasks=deferred,
            consecutive_failures=failures, last_error_code=error,
        ))

    def run_forever(self, *, max_cycles: int | None = None) -> ReviewerRecoveryServiceReport:
        if self._running:
            raise ReviewerRecoveryServiceError("reviewer_recovery_service_already_running")
        if max_cycles is not None and (type(max_cycles) is not int or max_cycles < 1):
            raise ValueError("invalid_reviewer_recovery_max_cycles")
        self._running = True
        cycles = recovered = deferred = failures = 0
        consecutive = 0
        last_error = None
        last_report = None
        try:
            self._emit("running", cycles, recovered, deferred, failures, None)
            while not self._stop.is_set() and (max_cycles is None or cycles < max_cycles):
                cycles += 1
                try:
                    last_report = self.supervisor.run_once(limit=self.batch_limit)
                    recovered += last_report.recovered_tasks
                    deferred += last_report.deferred_tasks
                    consecutive = 0
                    last_error = None
                    self._emit("running", cycles, recovered, deferred, failures, None)
                    delay = self.poll_interval_seconds
                except Exception as exc:
                    failures += 1
                    consecutive += 1
                    last_error = "recovery_cycle." + type(exc).__name__
                    self._emit("degraded", cycles, recovered, deferred, failures, last_error)
                    delay = min(
                        self.max_backoff_seconds,
                        self.poll_interval_seconds * (2 ** min(consecutive - 1, 16)),
                    )
                if max_cycles is not None and cycles >= max_cycles:
                    break
                self._stop.wait(delay)
            self._emit("stopped", cycles, recovered, deferred, failures, last_error)
            return ReviewerRecoveryServiceReport(
                cycles, recovered, deferred, failures, True, last_report,
            )
        finally:
            self._running = False


__all__ = [
    "ReviewerWorkerRecoveryService", "ReviewerRecoveryServiceError",
    "ReviewerRecoveryHeartbeat", "ReviewerRecoveryServiceReport",
]
