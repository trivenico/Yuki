"""Bounded orchestration for dependency-aware specialist plans."""

from __future__ import annotations

from dataclasses import dataclass

from automation.agent_coordinator import AgentPlan, SpecialistAgentCoordinator
from automation.specialist_worker import SpecialistTaskWorker


_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "blocked"})


@dataclass(frozen=True)
class PlanSupervisorResult:
    plan: AgentPlan
    executed_tasks: int
    rounds: int
    budget_exhausted: bool


class PlanSupervisor:
    """Advance dependency-ready work and execute only tasks belonging to one plan.

    Authorization remains in SpecialistAgentCoordinator and TaskRuntime. The
    worker's SQL claim predicate provides the plan boundary; this class never
    widens it to a global specialist queue.
    """

    def __init__(self, coordinator: SpecialistAgentCoordinator, worker: SpecialistTaskWorker):
        if not isinstance(coordinator, SpecialistAgentCoordinator):
            raise TypeError("coordinator must be a SpecialistAgentCoordinator.")
        if not isinstance(worker, SpecialistTaskWorker):
            raise TypeError("worker must be a SpecialistTaskWorker.")
        self.coordinator = coordinator
        self.worker = worker

    def run_plan(
        self, *, actor_id: str, plan_id: str, max_workers: int = 4,
        max_tasks: int = 20, max_rounds: int = 20,
    ) -> PlanSupervisorResult:
        """Run dependency waves under strict worker, task, and round ceilings.

        A queued task requiring approval is left untouched; the supervisor
        returns its current state rather than bypassing the approval gate.
        """
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200:
            raise ValueError("actor_id must be bounded non-empty text.")
        if not isinstance(plan_id, str) or not plan_id.strip() or len(plan_id) > 100 or "\x00" in plan_id:
            raise ValueError("plan_id must be bounded non-empty text.")
        if type(max_workers) is not int or not 1 <= max_workers <= 16:
            raise ValueError("max_workers must be an integer from 1 to 16.")
        if type(max_tasks) is not int or not 1 <= max_tasks <= 1000:
            raise ValueError("max_tasks must be an integer from 1 to 1000.")
        if type(max_rounds) is not int or not 1 <= max_rounds <= 100:
            raise ValueError("max_rounds must be an integer from 1 to 100.")

        plan_id = plan_id.strip()
        executed = 0
        rounds = 0
        plan = self.coordinator.get_plan(actor_id=actor_id, plan_id=plan_id)
        while rounds < max_rounds and not all(item.status in _TERMINAL for item in plan.subtasks):
            remaining = max_tasks - executed
            if remaining <= 0:
                break
            plan = self.coordinator.advance(actor_id=actor_id, plan_id=plan_id)
            rounds += 1
            batch = self.worker.run_concurrently(
                max_workers=max_workers, max_tasks=min(100, remaining), plan_id=plan_id,
            )
            executed += len(batch)
            if not batch:
                break
            plan = self.coordinator.get_plan(actor_id=actor_id, plan_id=plan_id)

        # Reconcile the last completed wave even when the task/round budget was
        # reached. This may dispatch newly dependency-ready work but never runs it.
        plan = self.coordinator.advance(actor_id=actor_id, plan_id=plan_id)
        budget_exhausted = (executed >= max_tasks or rounds >= max_rounds) and any(
            item.status not in _TERMINAL for item in plan.subtasks
        )
        return PlanSupervisorResult(
            plan=plan, executed_tasks=executed, rounds=rounds,
            budget_exhausted=budget_exhausted,
        )
