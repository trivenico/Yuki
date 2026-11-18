"""CEO orchestration: decompose a goal, preflight staffing, and queue a DAG.

This layer plans and assigns work only. It does not run employee models, execute
skills, approve side effects, or claim that queued work has completed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.durable_task_engine import DurableTaskEngine
from core.planner import PlanTask, Planner
from core.workforce import AgentRegistry
from core.workforce_router import NoEligibleAgent, WorkforceAssignment, WorkforceTaskRouter


@dataclass(frozen=True)
class CEOExecution:
    goal_id: str
    plan: tuple[PlanTask, ...]
    assignments: tuple[WorkforceAssignment, ...]


class WorkforceCEO:
    """Yuki's bounded CEO coordinator over the existing planner and task queue."""

    def __init__(
        self,
        *,
        planner: Planner,
        registry: AgentRegistry,
        task_engine: DurableTaskEngine,
        max_autonomy_tier: int = 1,
        require_independent_review: bool = False,
    ):
        if not isinstance(planner, Planner):
            raise TypeError("planner must be a Planner")
        if not isinstance(registry, AgentRegistry):
            raise TypeError("registry must be an AgentRegistry")
        if not isinstance(task_engine, DurableTaskEngine):
            raise TypeError("task_engine must be a DurableTaskEngine")
        if type(max_autonomy_tier) is not int or max_autonomy_tier not in range(4):
            raise ValueError("max_autonomy_tier must be an integer from 0 to 3")
        if type(require_independent_review) is not bool:
            raise ValueError("require_independent_review must be a boolean")
        self.planner = planner
        self.registry = registry
        self.task_engine = task_engine
        self.router = WorkforceTaskRouter(registry, task_engine)
        self.max_autonomy_tier = max_autonomy_tier
        self.require_independent_review = require_independent_review

    def plan_and_dispatch(self, goal: str, *, goal_id: Optional[str] = None) -> CEOExecution:
        """Preflight every task, then persist the plan as dependent workforce jobs."""
        plan = self.planner.decompose(goal)
        # Preflight all required capabilities before creating a goal or queueing
        # any work. This prevents predictable staffing failures from partial plans.
        reserved: dict[str, int] = {}
        planned_assignees: dict[str, str] = {}
        for task in plan:
            candidates = self.registry.recommend(
                task.required_capabilities,
                max_autonomy_tier=self.max_autonomy_tier,
                limit=100,
            )
            employee = next((
                candidate for candidate in candidates
                if candidate.active_tasks + reserved.get(candidate.employee_id, 0)
                < candidate.max_concurrent_tasks
            ), None)
            if employee is None:
                raise NoEligibleAgent(
                    f"No employee has remaining capacity for plan step '{task.task_id}' "
                    f"with capabilities {task.required_capabilities}."
                )
            planned_assignees[task.task_id] = employee.employee_id
            reserved[employee.employee_id] = reserved.get(employee.employee_id, 0) + 1

        active_goal_id = goal_id if goal_id is not None else self.task_engine.create_goal()
        if not isinstance(active_goal_id, str) or not active_goal_id.strip():
            raise ValueError("goal_id must be non-empty text")
        task_ids: dict[str, str] = {}
        assignments: list[WorkforceAssignment] = []
        for task in plan:
            dependencies = tuple(task_ids[dependency] for dependency in task.depends_on)
            assignment = self.router.submit(
                goal_id=active_goal_id,
                description=task.description,
                required_capabilities=task.required_capabilities,
                idempotency_key=f"ceo:{active_goal_id}:{task.task_id}",
                dependencies=dependencies,
                max_autonomy_tier=self.max_autonomy_tier,
                review_required=self.require_independent_review,
                preferred_employee_id=planned_assignees[task.task_id],
            )
            task_ids[task.task_id] = assignment.task_id
            assignments.append(assignment)
        return CEOExecution(active_goal_id, plan, tuple(assignments))

    def report(self, execution: CEOExecution) -> dict:
        """Return persisted task states; queued is not reported as completed."""
        if not isinstance(execution, CEOExecution):
            raise TypeError("execution must be a CEOExecution")
        tasks = []
        for assignment in execution.assignments:
            task = self.task_engine.get_task(assignment.task_id)
            if task is None:
                state = "missing"
            else:
                state = task["state"]
            tasks.append({
                "task_id": assignment.task_id,
                "employee_id": assignment.employee_id,
                "employee_name": assignment.employee_name,
                "department": assignment.department,
                "state": state,
            })
        counts: dict[str, int] = {}
        departments: dict[str, dict[str, int]] = {}
        for task in tasks:
            state = task["state"]
            counts[state] = counts.get(state, 0) + 1
            department = task["department"]
            department_counts = departments.setdefault(department, {"total": 0})
            department_counts["total"] += 1
            department_counts[state] = department_counts.get(state, 0) + 1
        return {
            "goal_id": execution.goal_id,
            "total_tasks": len(tasks),
            "counts": counts,
            "departments": departments,
            "tasks": tasks,
            "complete": bool(tasks) and all(task["state"] == "succeeded" for task in tasks),
        }
