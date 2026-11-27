"""Finite-cycle supervision and explicit expired-lease recovery for Yuki workers.

This module does not create background threads or automatically retry failed or
interrupted tasks. Run one cycle from an explicitly managed service/scheduler.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from threading import Condition, Event
from typing import Callable, Mapping, Any

from automation.specialist_worker import SpecialistTaskWorker
from automation.task_runtime import TaskRuntime


@dataclass(frozen=True)
class WorkerCycleReport:
    recovered_task_ids: tuple[str, ...]
    processed_count: int
    succeeded_count: int
    failed_count: int
    idle: bool
    resource_paused: bool = False


class WorkerSupervisor:
    """Run a bounded cycle: journal expired leases, then process a finite task batch."""

    def __init__(
        self,
        runtime: TaskRuntime,
        worker: SpecialistTaskWorker,
        *,
        max_tasks_per_cycle: int = 10,
        recovery_limit: int = 50,
        max_workers: int = 1,
        resource_guard: Callable[[], bool] | None = None,
        health_publisher: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        if not isinstance(runtime, TaskRuntime):
            raise TypeError("runtime must be a TaskRuntime.")
        if not isinstance(worker, SpecialistTaskWorker):
            raise TypeError("worker must be a SpecialistTaskWorker.")
        if worker.runtime is not runtime:
            raise ValueError("worker and supervisor must share the same TaskRuntime instance.")
        if type(max_tasks_per_cycle) is not int or not 1 <= max_tasks_per_cycle <= 100:
            raise ValueError("max_tasks_per_cycle must be an integer from 1 to 100.")
        if type(recovery_limit) is not int or not 1 <= recovery_limit <= 100:
            raise ValueError("recovery_limit must be an integer from 1 to 100.")
        self.runtime = runtime
        self.worker = worker
        self.max_tasks_per_cycle = max_tasks_per_cycle
        self.recovery_limit = recovery_limit
        if type(max_workers) is not int or not 1 <= max_workers <= 16:
            raise ValueError("max_workers must be an integer from 1 to 16.")
        self.max_workers = max_workers
        if resource_guard is not None and not callable(resource_guard):
            raise TypeError("resource_guard must be callable when supplied.")
        self.resource_guard = resource_guard
        if health_publisher is not None and not callable(health_publisher):
            raise TypeError("health_publisher must be callable when supplied.")
        self.health_publisher = health_publisher
        self._condition = Condition()
        self._stop_event = Event()
        self._active_cycles = 0
        self._draining = False
        self._health_sequence = 0
        self._next_health_sequence = 0
        self._health_condition = Condition()

    def _publish_health(self, phase: str, **details: Any) -> None:
        """Publish diagnostics best-effort; snapshots never control execution."""
        if self.health_publisher is None:
            return
        try:
            self.health_publisher(phase, details)
        except Exception:
            # A diagnostics sink must not turn a task cycle into a failure.
            pass

    def _reserve_health_event_locked(self) -> int:
        """Reserve publication order while holding the supervisor condition."""
        sequence = self._health_sequence
        self._health_sequence += 1
        return sequence

    def _publish_health_event(
        self, sequence: int, phase: str, **details: Any,
    ) -> None:
        """Publish in reserved event order without holding supervisor locks."""
        if self.health_publisher is None:
            return
        with self._health_condition:
            self._health_condition.wait_for(
                lambda: sequence == self._next_health_sequence
            )
            try:
                self._publish_health(phase, **details)
            finally:
                self._next_health_sequence += 1
                self._health_condition.notify_all()

    def run_cycle(self) -> WorkerCycleReport:
        """Recover expired leases and process a bounded batch unless draining."""
        with self._condition:
            if self._draining:
                raise RuntimeError("worker supervisor is draining and no longer accepts cycles")
            self._active_cycles += 1
            start_sequence = self._reserve_health_event_locked()
            active_cycles = self._active_cycles
        self._publish_health_event(
            start_sequence, "running", active_cycles=active_cycles,
        )
        terminal_phase = "error"
        terminal_details: dict[str, Any] = {}
        try:
            report = self._run_cycle()
            terminal_phase = (
                "paused" if report.resource_paused
                else "idle" if report.idle
                else "completed"
            )
            terminal_details = {
                "processed_count": report.processed_count,
                "succeeded_count": report.succeeded_count,
                "failed_count": report.failed_count,
                "resource_paused": report.resource_paused,
            }
            return report
        except Exception as exc:
            terminal_details = {"error_type": type(exc).__name__}
            raise
        finally:
            with self._condition:
                self._active_cycles -= 1
                if self._active_cycles == 0:
                    self._condition.notify_all()
                current_phase = "draining" if self._draining else (
                    "running" if self._active_cycles else "idle"
                )
                remaining_cycles = self._active_cycles
                final_sequence = self._reserve_health_event_locked()
            # Snapshot I/O must never hold the condition used by task claims,
            # cycle admission, or shutdown coordination. Reserved sequence
            # numbers prevent concurrent publishers from overwriting newer state.
            self._publish_health_event(
                final_sequence,
                current_phase,
                active_cycles=remaining_cycles,
                last_cycle_phase=terminal_phase,
                **terminal_details,
            )

    def _run_cycle(self) -> WorkerCycleReport:
        if self.resource_guard is not None:
            try:
                resources_available = self.resource_guard() is True
            except Exception:
                resources_available = False
            if not resources_available:
                return WorkerCycleReport(
                    recovered_task_ids=(), processed_count=0,
                    succeeded_count=0, failed_count=0, idle=True,
                    resource_paused=True,
                )
        recovered = self.runtime.recover_expired(
            actor_id=self.worker.service_actor_id,
            limit=self.recovery_limit,
        )
        if self.max_workers == 1:
            results = []
            for _ in range(self.max_tasks_per_cycle):
                result = self.worker.run_once(
                    stop_event=self._stop_event, claim_guard=self._condition,
                )
                if result is None:
                    break
                results.append(result)
        else:
            results = list(self.worker.run_concurrently(
                max_workers=self.max_workers,
                max_tasks=self.max_tasks_per_cycle,
                stop_event=self._stop_event, claim_guard=self._condition,
            ))
        processed = len(results)
        succeeded = sum(result.state == "succeeded" for result in results)
        failed = sum(result.state == "failed" for result in results)
        idle = processed < self.max_tasks_per_cycle and not self._stop_event.is_set()
        return WorkerCycleReport(
            recovered_task_ids=tuple(record.task_id for record in recovered),
            processed_count=processed,
            succeeded_count=succeeded,
            failed_count=failed,
            idle=idle,
        )

    def shutdown(self, *, timeout_seconds: float = 30.0) -> bool:
        """Stop new cycles/claims and wait for active handlers to unwind.

        Returns False on timeout. A timeout does not cancel a synchronous
        provider call or make it safe to close the task store/provider beneath it.
        The caller must keep dependencies alive until a later successful drain.
        """
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise TypeError("timeout_seconds must be a finite number")
        if not 0 <= timeout_seconds <= 3600 or not isfinite(timeout_seconds):
            raise ValueError("timeout_seconds must be between 0 and 3600 seconds")
        with self._condition:
            self._draining = True
            self._stop_event.set()
            active_cycles = self._active_cycles
            draining_sequence = self._reserve_health_event_locked()
        self._publish_health_event(
            draining_sequence, "draining", active_cycles=active_cycles,
        )
        with self._condition:
            drained = self._condition.wait_for(
                lambda: self._active_cycles == 0,
                timeout=float(timeout_seconds),
            )
            active_cycles = self._active_cycles
            stopped_sequence = (
                self._reserve_health_event_locked() if drained else None
            )
        if stopped_sequence is not None:
            self._publish_health_event(
                stopped_sequence, "stopped", active_cycles=active_cycles,
            )
        return drained

    @property
    def draining(self) -> bool:
        with self._condition:
            return self._draining

