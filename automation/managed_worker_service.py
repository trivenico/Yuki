"""Explicit lifecycle gate for bounded specialist worker cycles.

This service owns no background thread or scheduler. The owner must start it
and explicitly request each finite supervisor cycle. Stop drains an in-flight
cycle; it never pretends to cancel a provider request already in progress.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Condition
from time import monotonic

from automation.worker_supervisor import WorkerCycleReport, WorkerSupervisor

_STATES = frozenset({"stopped", "running", "paused", "stopping"})


@dataclass(frozen=True)
class WorkerServiceStatus:
    state: str
    cycle_active: bool
    completed_cycles: int
    failed_cycles: int
    last_error: str | None
    last_report: WorkerCycleReport | None


class ManagedWorkerService:
    """Thread-safe lifecycle gate for explicitly requested worker cycles."""

    def __init__(self, supervisor: WorkerSupervisor) -> None:
        if not isinstance(supervisor, WorkerSupervisor):
            raise TypeError("supervisor must be a WorkerSupervisor.")
        self.supervisor = supervisor
        self._condition = Condition()
        self._state = "stopped"
        self._cycle_active = False
        self._completed_cycles = 0
        self._failed_cycles = 0
        self._last_error: str | None = None
        self._last_report: WorkerCycleReport | None = None

    @property
    def status(self) -> WorkerServiceStatus:
        with self._condition:
            return WorkerServiceStatus(
                state=self._state,
                cycle_active=self._cycle_active,
                completed_cycles=self._completed_cycles,
                failed_cycles=self._failed_cycles,
                last_error=self._last_error,
                last_report=self._last_report,
            )

    def start(self) -> WorkerServiceStatus:
        """Open cycle admission. Does not create threads or run work."""
        with self._condition:
            if self._state != "stopped":
                raise RuntimeError(f"cannot start worker service from {self._state}.")
            self._state = "running"
            self._last_error = None
            self._condition.notify_all()
            return self.status

    def pause(self) -> WorkerServiceStatus:
        """Reject new cycles while allowing the current finite cycle to finish."""
        with self._condition:
            if self._state != "running":
                raise RuntimeError(f"cannot pause worker service from {self._state}.")
            self._state = "paused"
            self._condition.notify_all()
            return self.status

    def resume(self) -> WorkerServiceStatus:
        with self._condition:
            if self._state != "paused":
                raise RuntimeError(f"cannot resume worker service from {self._state}.")
            self._state = "running"
            self._condition.notify_all()
            return self.status

    def run_cycle(self) -> WorkerCycleReport:
        """Run one explicitly requested bounded cycle if admission is open."""
        with self._condition:
            if self._state != "running":
                raise RuntimeError(f"worker service does not accept cycles while {self._state}.")
            if self._cycle_active:
                raise RuntimeError("a worker cycle is already active.")
            self._cycle_active = True
        try:
            report = self.supervisor.run_cycle()
        except Exception as exc:
            with self._condition:
                self._failed_cycles += 1
                self._last_error = type(exc).__name__
            raise
        else:
            with self._condition:
                self._completed_cycles += 1
                self._last_report = report
                self._last_error = None
            return report
        finally:
            with self._condition:
                self._cycle_active = False
                if self._state == "stopping":
                    self._state = "stopped"
                self._condition.notify_all()

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        """Wait for the active finite cycle to finish; no work is cancelled."""
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0):
            raise ValueError("timeout must be a non-negative number or None.")
        deadline = None if timeout is None else monotonic() + timeout
        with self._condition:
            while self._cycle_active:
                remaining = None if deadline is None else deadline - monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def stop(self, timeout: float | None = None) -> bool:
        """Close cycle admission and drain the current cycle.

        Returns False on timeout. The service remains in stopping state and
        transitions to stopped when the active cycle exits; stop may be retried.
        """
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0):
            raise ValueError("timeout must be a non-negative number or None.")
        deadline = None if timeout is None else monotonic() + timeout
        with self._condition:
            if self._state == "stopped":
                return True
            self._state = "stopping"
            while self._cycle_active:
                remaining = None if deadline is None else deadline - monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            self._state = "stopped"
            self._condition.notify_all()
            return True

    close = stop
