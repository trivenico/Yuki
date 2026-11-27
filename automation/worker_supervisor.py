"""Finite-cycle supervision and explicit expired-lease recovery for Yuki workers.

This module does not create background threads or automatically retry failed or
interrupted tasks. Run one cycle from an explicitly managed service/scheduler.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from threading import Condition, Event
from typing import Callable

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
        self._condition = Condition()
        self._stop_event = Event()
        self._active_cycles = 0
        self._draining = False

    def run_cycle(self) -> WorkerCycleReport:
        """Recover expired leases and process a bounded batch unless draining."""
        with self._condition:
            if self._draining:
                raise RuntimeError("worker supervisor is draining and no longer accepts cycles")
            self._active_cycles += 1
        try:
            return self._run_cycle()
        finally:
            with self._condition:
                self._active_cycles -= 1
                if self._active_cycles == 0:
                    self._condition.notify_all()

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
            return self._condition.wait_for(
                lambda: self._active_cycles == 0,
                timeout=float(timeout_seconds),
            )

    @property
    def draining(self) -> bool:
        with self._condition:
            return self._draining

