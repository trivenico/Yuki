"""Bridge workforce assignment recommendations into Yuki's durable task queue.

This adapter only creates queued work. It never claims, executes, or authorizes
the queued task; the worker must still enforce its own permission/approval gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from core.durable_task_engine import DurableTaskEngine
from core.workforce import AgentRegistry, AgentProfile


class NoEligibleAgent(RuntimeError):
    """No available profile satisfies the task's declared constraints."""


@dataclass(frozen=True)
class WorkforceAssignment:
    task_id: str
    goal_id: str
    employee_id: str
    employee_name: str
    department: str
    required_capabilities: tuple[str, ...]
    required_permissions: tuple[str, ...]
    autonomy_tier: int


class WorkforceTaskRouter:
    """Assign a task to one eligible employee and persist it in the durable queue."""

    def __init__(self, registry: AgentRegistry, task_engine: DurableTaskEngine):
        if not isinstance(registry, AgentRegistry):
            raise TypeError("registry must be an AgentRegistry")
        if not isinstance(task_engine, DurableTaskEngine):
            raise TypeError("task_engine must be a DurableTaskEngine")
        self.registry = registry
        self.task_engine = task_engine

    def submit(
        self,
        *,
        goal_id: str,
        description: str,
        required_capabilities: Iterable[str],
        idempotency_key: str,
        required_permissions: Iterable[str] = (),
        max_autonomy_tier: int = 1,
        dependencies: Iterable[str] = (),
        max_attempts: int = 1,
    ) -> WorkforceAssignment:
        if not isinstance(description, str) or not description.strip() or len(description) > 20_000:
            raise ValueError("description must be non-empty and at most 20000 characters")
        capabilities = tuple(sorted(set(required_capabilities)))
        permissions = tuple(sorted(set(required_permissions)))
        if not capabilities or any(not isinstance(x, str) or not x.strip() for x in capabilities):
            raise ValueError("required_capabilities must contain non-empty strings")
        if any(not isinstance(x, str) or not x.strip() for x in permissions):
            raise ValueError("required_permissions must contain non-empty strings")
        if max_autonomy_tier not in range(4):
            raise ValueError("max_autonomy_tier must be between 0 and 3")

        candidates = self.registry.recommend(
            capabilities,
            required_permissions=permissions,
            max_autonomy_tier=max_autonomy_tier,
            limit=1,
        )
        if not candidates:
            raise NoEligibleAgent("No available employee satisfies task capability, permission, capacity, and autonomy constraints.")
        employee: AgentProfile = candidates[0]

        # Store the policy requirements and selected identity for downstream
        # workers to re-check. Selection is not an execution authorization.
        task = self.task_engine.submit(
            goal_id,
            "workforce.task",
            {
                "description": description.strip(),
                "assigned_employee_id": employee.employee_id,
                "required_capabilities": list(capabilities),
                "required_permissions": list(permissions),
                "max_autonomy_tier": max_autonomy_tier,
                "execution_authorized": False,
            },
            idempotency_key=idempotency_key,
            dependencies=tuple(dependencies),
            max_attempts=max_attempts,
        )
        return WorkforceAssignment(
            task_id=task["id"],
            goal_id=goal_id,
            employee_id=employee.employee_id,
            employee_name=employee.name,
            department=employee.department,
            required_capabilities=capabilities,
            required_permissions=permissions,
            autonomy_tier=employee.autonomy_tier,
        )
