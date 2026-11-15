"""Bounded orchestration facade for dependency-aware specialist execution."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping

from automation.agent_coordinator import AgentPlan, SpecialistAgentCoordinator, SubtaskSpec
from automation.specialist_worker import SpecialistTaskWorker
from automation.task_runtime import TaskRecord


_MAX_ROUNDS = 20
_MAX_TASKS = 100
_MAX_OUTPUT_CHARS = 20_000
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "blocked"})


@dataclass(frozen=True)
class SpecialistExecutionReport:
    """Immutable snapshot of a bounded supervisor execution attempt."""

    plan: AgentPlan
    worker_results: tuple[TaskRecord, ...]
    validated_outputs: Mapping[str, str]
    rounds: int
    complete: bool
    awaiting_approval: bool
    failed_subtasks: tuple[str, ...]
    remaining_subtasks: tuple[str, ...]


class SpecialistSupervisor:
    """Coordinate planning, dependency dispatch, bounded execution and validation.

    The supervisor deliberately delegates authorization, durable state transitions,
    approval gates, task leases, and specialist selection to the existing coordinator
    and worker. It never invokes specialist handlers directly.
    """

    def __init__(
        self,
        coordinator: SpecialistAgentCoordinator,
        worker: SpecialistTaskWorker,
    ) -> None:
        if not isinstance(coordinator, SpecialistAgentCoordinator):
            raise TypeError("coordinator must be a SpecialistAgentCoordinator.")
        if not isinstance(worker, SpecialistTaskWorker):
            raise TypeError("worker must be a SpecialistTaskWorker.")
        if coordinator.runtime is not worker.runtime:
            raise ValueError("coordinator and worker must share the same TaskRuntime.")
        self.coordinator = coordinator
        self.worker = worker

    def run(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        subtasks: Iterable[SubtaskSpec],
        max_workers: int = 4,
        max_tasks: int = 100,
        max_rounds: int = 20,
    ) -> SpecialistExecutionReport:
        """Create an idempotent plan and drive ready work within explicit budgets."""
        plan = self.coordinator.submit(
            actor_id=actor_id, idempotency_key=idempotency_key, subtasks=subtasks,
        )
        return self._drive(
            actor_id=actor_id, plan_id=plan.plan_id,
            max_workers=max_workers, max_tasks=max_tasks, max_rounds=max_rounds,
        )

    def resume(
        self,
        *,
        actor_id: str,
        plan_id: str,
        max_workers: int = 4,
        max_tasks: int = 100,
        max_rounds: int = 20,
    ) -> SpecialistExecutionReport:
        """Resume an existing plan after approval or an external worker interval."""
        return self._drive(
            actor_id=actor_id, plan_id=plan_id,
            max_workers=max_workers, max_tasks=max_tasks, max_rounds=max_rounds,
        )

    def _drive(
        self,
        *,
        actor_id: str,
        plan_id: str,
        max_workers: int,
        max_tasks: int,
        max_rounds: int,
    ) -> SpecialistExecutionReport:
        if type(max_workers) is not int or not 1 <= max_workers <= 16:
            raise ValueError("max_workers must be between 1 and 16.")
        if type(max_tasks) is not int or not 1 <= max_tasks <= _MAX_TASKS:
            raise ValueError("max_tasks must be between 1 and 100.")
        if type(max_rounds) is not int or not 1 <= max_rounds <= _MAX_ROUNDS:
            raise ValueError("max_rounds must be between 1 and 20.")

        worker_results: list[TaskRecord] = []
        rounds = 0
        for _ in range(max_rounds):
            plan = self.coordinator.advance(actor_id=actor_id, plan_id=plan_id)
            if all(item.status in _TERMINAL for item in plan.subtasks):
                break
            remaining_budget = max_tasks - len(worker_results)
            if remaining_budget <= 0:
                break
            assigned_task_ids = tuple(
                item.task_id for item in plan.subtasks
                if item.task_id is not None and item.status == "queued"
            )
            batch = self.worker.run_concurrently(
                max_workers=max_workers, max_tasks=remaining_budget,
                task_ids=assigned_task_ids,
            )
            rounds += 1
            worker_results.extend(batch)
            plan = self.coordinator.advance(actor_id=actor_id, plan_id=plan_id)
            if all(item.status in _TERMINAL for item in plan.subtasks):
                break
            if not batch:
                # Usually an approval gate or no currently claimable work. Do not
                # spin; the caller can approve and resume this durable plan later.
                break

        plan = self.coordinator.get_plan(actor_id=actor_id, plan_id=plan_id)
        outputs: dict[str, str] = {}
        failed: list[str] = []
        remaining: list[str] = []
        awaiting_approval = False
        for item in plan.subtasks:
            if item.status in {"failed", "cancelled", "blocked"}:
                failed.append(item.subtask_id)
            if item.status not in _TERMINAL:
                remaining.append(item.subtask_id)
            if item.status == "queued" and item.task_id:
                pending_task = self.coordinator.runtime.get(
                    actor_id=actor_id, task_id=item.task_id,
                )
                if pending_task.state == "queued" and any(
                    step.get("requires_approval") is True for step in pending_task.steps
                ):
                    awaiting_approval = True
            if item.status != "succeeded":
                continue
            if not item.task_id:
                raise RuntimeError("successful specialist subtask has no durable task ID.")
            task = self.coordinator.runtime.get(actor_id=actor_id, task_id=item.task_id)
            result = task.result
            if task.state != "succeeded" or not isinstance(result, dict):
                raise RuntimeError("specialist result state does not match the plan.")
            output = result.get("output")
            if (
                result.get("specialist") != item.agent_id
                or not isinstance(output, str)
                or not output.strip()
                or len(output) > _MAX_OUTPUT_CHARS
                or "\x00" in output
            ):
                raise RuntimeError("specialist result failed supervisor validation.")
            outputs[item.subtask_id] = output.strip()

        complete = all(item.status in _TERMINAL for item in plan.subtasks)
        return SpecialistExecutionReport(
            plan=plan,
            worker_results=tuple(worker_results),
            validated_outputs=MappingProxyType(outputs),
            rounds=rounds,
            complete=complete,
            awaiting_approval=awaiting_approval,
            failed_subtasks=tuple(failed),
            remaining_subtasks=tuple(remaining),
        )
