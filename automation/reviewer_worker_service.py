"""Supervise reviewer worker polling, concurrency, heartbeats, and shutdown.

This service is intentionally process-local. Deploy exactly one supervisor per
reviewer identity unless a shared concurrency authority is added. SQLite
outbox claims remain the cross-process duplicate-dispatch guard.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from typing import Callable


class ReviewerWorkerServiceError(RuntimeError):
    """The reviewer worker service could not maintain its runtime contract."""


@dataclass(frozen=True)
class ReviewerWorkerHeartbeat:
    worker_id: str
    state: str
    observed_at: str
    active_slots: int
    max_concurrency: int
    completed_dispatches: int
    failed_cycles: int
    last_error_code: str | None


@dataclass(frozen=True)
class ReviewerWorkerServiceReport:
    worker_id: str
    cycles: int
    completed_dispatches: int
    failed_cycles: int
    stopped: bool


class ReviewerWorkerService:
    """Run one reviewer worker with bounded parallel polling and health reports.

    heartbeat_callback must publish to trusted host monitoring. It receives no
    credentials or task payloads. stop() prevents new cycles; in-flight calls
    are allowed to finish before run_forever returns.
    """

    def __init__(
        self,
        worker,
        *,
        heartbeat_callback: Callable[[ReviewerWorkerHeartbeat], None],
        max_concurrency: int = 1,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        if not callable(getattr(worker, "run_once", None)):
            raise TypeError("reviewer_worker_run_once_required")
        worker_id = getattr(worker, "worker_id", None)
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError("reviewer_worker_identity_required")
        if not callable(heartbeat_callback):
            raise TypeError("reviewer_heartbeat_callback_required")
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 32:
            raise ValueError("invalid_reviewer_worker_concurrency")
        if (isinstance(poll_interval_seconds, bool)
                or not isinstance(poll_interval_seconds, (int, float))
                or not 0.05 <= poll_interval_seconds <= 300):
            raise ValueError("invalid_reviewer_worker_poll_interval")
        self.worker = worker
        self.worker_id = worker_id
        self.heartbeat_callback = heartbeat_callback
        self.max_concurrency = max_concurrency
        self.poll_interval_seconds = float(poll_interval_seconds)
        self._stop = Event()
        self._running = False

    def stop(self) -> None:
        """Request graceful shutdown; already-running executions are not killed."""
        self._stop.set()

    def _heartbeat(
        self, *, state: str, active_slots: int,
        completed: int, failures: int, last_error: str | None,
    ) -> None:
        self.heartbeat_callback(ReviewerWorkerHeartbeat(
            worker_id=self.worker_id,
            state=state,
            observed_at=datetime.now(timezone.utc).isoformat(),
            active_slots=active_slots,
            max_concurrency=self.max_concurrency,
            completed_dispatches=completed,
            failed_cycles=failures,
            last_error_code=last_error,
        ))

    def run_forever(self) -> ReviewerWorkerServiceReport:
        if self._running:
            raise ReviewerWorkerServiceError("reviewer_worker_service_already_running")
        self._running = True
        cycles = completed = failures = 0
        last_error = None
        try:
            with ThreadPoolExecutor(
                max_workers=self.max_concurrency,
                thread_name_prefix="yuki-reviewer",
            ) as pool:
                while not self._stop.is_set():
                    futures = [
                        pool.submit(self.worker.run_once, limit=1)
                        for _ in range(self.max_concurrency)
                    ]
                    cycles += 1
                    done, _ = wait(futures, return_when=ALL_COMPLETED)
                    claimed_this_cycle = 0
                    for future in done:
                        try:
                            report = future.result()
                            claimed_this_cycle += int(getattr(report, "claimed", 0))
                            completed += int(getattr(report, "completed", 0))
                        except Exception as exc:
                            failures += 1
                            last_error = "worker_cycle." + type(exc).__name__
                    self._heartbeat(
                        state="stopping" if self._stop.is_set() else "running",
                        active_slots=0,
                        completed=completed,
                        failures=failures,
                        last_error=last_error,
                    )
                    if self._stop.is_set():
                        break
                    if claimed_this_cycle == 0:
                        self._stop.wait(self.poll_interval_seconds)
            self._heartbeat(
                state="stopped", active_slots=0,
                completed=completed, failures=failures,
                last_error=last_error,
            )
            return ReviewerWorkerServiceReport(
                worker_id=self.worker_id, cycles=cycles,
                completed_dispatches=completed, failed_cycles=failures,
                stopped=True,
            )
        finally:
            self._running = False


__all__ = [
    "ReviewerWorkerService", "ReviewerWorkerServiceError",
    "ReviewerWorkerHeartbeat", "ReviewerWorkerServiceReport",
]
