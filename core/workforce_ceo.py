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

    def _verified_knowledge_for(self, capabilities: tuple[str, ...]) -> list[dict]:
        """Return a small, provenance-carrying context from verified lessons only."""
        selected: list[dict] = []
        seen: set[str] = set()
        for domain in (*sorted(set(capabilities)), "workforce"):
            for record in self.task_engine.list_verified_knowledge(limit=5, domain=domain):
                knowledge_id = record.get("knowledge_id")
                lesson = record.get("knowledge")
                if (not isinstance(knowledge_id, str) or knowledge_id in seen
                        or not isinstance(lesson, dict)):
                    continue
                selected.append({
                    "knowledge_id": knowledge_id,
                    "title": lesson["title"][:200],
                    "lesson": lesson["lesson"][:1000],
                    "source_task_id": record["source_task_id"],
                    "reviewed_by": record["reviewed_by"],
                })
                seen.add(knowledge_id)
                if len(selected) >= 5:
                    return selected
        return selected

    def plan_and_dispatch(self, goal: str, *, goal_id: Optional[str] = None) -> CEOExecution:
        """Preflight and idempotently reconcile every step of a persisted DAG.

        Existing task assignments are reused on retry. This is important because
        a worker may start an early task, or a previous request may have failed
        after only part of the DAG was submitted. Re-selecting an employee would
        change the task fingerprint and turn a safe retry into a conflict.
        """
        if goal_id is not None and (
            not isinstance(goal_id, str) or not goal_id.strip()
        ):
            raise ValueError("goal_id must be non-empty text")
        plan = self.planner.decompose(goal)
        knowledge_by_task = {
            task.task_id: self._verified_knowledge_for(tuple(task.required_capabilities))
            for task in plan
        }
        if goal_id is not None:
            # Bind the exact normalized DAG before any assignments are written.
            # A changed planner output on retry must not silently orphan or
            # mutate already queued work under the same durable goal.
            self.task_engine.bind_goal_plan(
                goal_id,
                [
                    {
                        "task_id": task.task_id,
                        "description": task.description,
                        "required_capabilities": sorted(set(task.required_capabilities)),
                        "depends_on": list(task.depends_on),
                        "knowledge_context": knowledge_by_task[task.task_id],
                    }
                    for task in plan
                ],
            )

        reserved: dict[str, int] = {}
        planned_assignees: dict[str, str] = {}
        existing_tasks: dict[str, dict] = {}
        for task in plan:
            task_key = f"ceo:{goal_id}:{task.task_id}" if goal_id is not None else None
            existing = (
                self.task_engine.get_task_by_idempotency(goal_id, task_key)
                if task_key is not None else None
            )
            if existing is not None:
                payload = existing["payload"]
                employee_id = payload.get("assigned_employee_id")
                employee = self.registry.get(employee_id) if isinstance(employee_id, str) else None
                expected_capabilities = tuple(sorted(set(task.required_capabilities)))
                if (
                    employee is None
                    or employee.status in {"offline", "disabled"}
                    or not set(expected_capabilities).issubset(employee.capabilities)
                    or employee.autonomy_tier > self.max_autonomy_tier
                    or payload.get("description") != task.description.strip()
                    or tuple(sorted(payload.get("required_capabilities", ()))) != expected_capabilities
                    or tuple(sorted(payload.get("required_permissions", ()))) != ()
                    or payload.get("max_autonomy_tier") != self.max_autonomy_tier
                    or payload.get("review_required") is not self.require_independent_review
                    or payload.get("knowledge_context", []) != knowledge_by_task[task.task_id]
                ):
                    raise NoEligibleAgent(
                        f"Persisted assignment for plan step '{task.task_id}' no longer matches current policy."
                    )
                existing_tasks[task.task_id] = existing
                planned_assignees[task.task_id] = employee.employee_id
                reserved[employee.employee_id] = reserved.get(employee.employee_id, 0) + 1
                continue

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
        task_ids: dict[str, str] = {}
        assignments: list[WorkforceAssignment] = []
        for task in plan:
            dependencies = tuple(task_ids[dependency] for dependency in task.depends_on)
            existing = existing_tasks.get(task.task_id)
            if existing is not None:
                if tuple(existing.get("dependencies", ())) != tuple(sorted(dependencies)):
                    raise ValueError(
                        f"Persisted dependencies for plan step '{task.task_id}' do not match the current plan."
                    )
                employee = self.registry.get(planned_assignees[task.task_id])
                assignment = WorkforceAssignment(
                    task_id=existing["id"],
                    goal_id=active_goal_id,
                    employee_id=employee.employee_id,
                    employee_name=employee.name,
                    department=employee.department,
                    required_capabilities=tuple(sorted(set(task.required_capabilities))),
                    required_permissions=(),
                    autonomy_tier=employee.autonomy_tier,
                )
            else:
                assignment = self.router.submit(
                    goal_id=active_goal_id,
                    description=task.description,
                    required_capabilities=task.required_capabilities,
                    idempotency_key=f"ceo:{active_goal_id}:{task.task_id}",
                    dependencies=dependencies,
                    max_autonomy_tier=self.max_autonomy_tier,
                    review_required=self.require_independent_review,
                    preferred_employee_id=planned_assignees[task.task_id],
                    knowledge_context=knowledge_by_task[task.task_id],
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
