"""Finite-cycle supervision and explicit expired-lease recovery for Yuki workers.

This module does not create background threads or automatically retry failed or
interrupted tasks. Run one cycle from an explicitly managed service/scheduler.
"""
from __future__ import annotations

from dataclasses import dataclass

from automation.specialist_worker import SpecialistTaskWorker
from automation.task_runtime import TaskRuntime


@dataclass(frozen=True)
class WorkerCycleReport:
    recovered_task_ids: tuple[str, ...]
    processed_count: int
    succeeded_count: int
    failed_count: int
    idle: bool


class WorkerSupervisor:
    """Run a bounded cycle: journal expired leases, then process a finite task batch."""

    def __init__(
        self,
        runtime: TaskRuntime,
        worker: SpecialistTaskWorker,
        *,
        max_tasks_per_cycle: int = 10,
        recovery_limit: int = 50,
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

    def run_cycle(self) -> WorkerCycleReport:
        """Recover expired leases and process at most the configured number of tasks."""
        recovered = self.runtime.recover_expired(
            actor_id=self.worker.service_actor_id,
            limit=self.recovery_limit,
        )
        processed = succeeded = failed = 0
        idle = False
        for _ in range(self.max_tasks_per_cycle):
            result = self.worker.run_once()
            if result is None:
                idle = True
                break
            processed += 1
            if result.state == "succeeded":
                succeeded += 1
            elif result.state == "failed":
                failed += 1
        return WorkerCycleReport(
            recovered_task_ids=tuple(record.task_id for record in recovered),
            processed_count=processed,
            succeeded_count=succeeded,
            failed_count=failed,
            idle=idle,
        )
