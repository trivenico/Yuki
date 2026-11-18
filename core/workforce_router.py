"""Bridge workforce assignment recommendations into Yuki's durable task queue.

This adapter only creates queued work. It never claims, executes, or authorizes
the queued task; the worker must still enforce its own permission/approval gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

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


def _normalize_names(value: Iterable[str], field_name: str, *, required: bool) -> tuple[str, ...]:
    """Validate before deduplication so malformed/unhashable input fails predictably."""
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field_name} must be an iterable of non-empty strings, not text")
    try:
        items = tuple(value)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be an iterable of non-empty strings") from exc
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError(f"{field_name} must contain only non-empty strings")
    normalized = tuple(sorted(set(items)))
    if required and not normalized:
        raise ValueError(f"{field_name} must contain at least one value")
    return normalized


def _normalize_dependencies(value: Iterable[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError("dependencies must be an iterable of task ID strings")
    try:
        items = tuple(value)
    except TypeError as exc:
        raise ValueError("dependencies must be an iterable of task ID strings") from exc
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError("dependencies must contain only non-empty task ID strings")
    return tuple(dict.fromkeys(items))


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
        review_required: bool = False,
        preferred_employee_id: Optional[str] = None,
    ) -> WorkforceAssignment:
        if not isinstance(goal_id, str) or not goal_id.strip():
            raise ValueError("goal_id must be non-empty text")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("idempotency_key must be non-empty text")
        if not isinstance(description, str) or not description.strip() or len(description) > 20_000:
            raise ValueError("description must be non-empty and at most 20000 characters")
        capabilities = _normalize_names(required_capabilities, "required_capabilities", required=True)
        permissions = _normalize_names(required_permissions, "required_permissions", required=False)
        dependency_ids = _normalize_dependencies(dependencies)
        if type(max_autonomy_tier) is not int or max_autonomy_tier not in range(4):
            raise ValueError("max_autonomy_tier must be an integer between 0 and 3")
        if type(review_required) is not bool:
            raise ValueError("review_required must be a boolean")
        if type(max_attempts) is not int or max_attempts != 1:
            raise ValueError("workforce tasks must use max_attempts=1; uncertain side effects require manual review")

        if preferred_employee_id is not None and (
            not isinstance(preferred_employee_id, str) or not preferred_employee_id.strip()
        ):
            raise ValueError("preferred_employee_id must be non-empty text when provided")
        candidates = self.registry.recommend(
            capabilities,
            required_permissions=permissions,
            max_autonomy_tier=max_autonomy_tier,
            limit=100,
        )
        if preferred_employee_id is not None:
            candidates = tuple(
                employee for employee in candidates
                if employee.employee_id == preferred_employee_id
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
                "review_required": review_required,
            },
            idempotency_key=idempotency_key,
            dependencies=dependency_ids,
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
