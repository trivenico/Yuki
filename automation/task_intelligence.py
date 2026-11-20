"""Task-intelligence boundary for preparing reviewable specialist plans.

This module accepts an explicit, structured decomposition and asks the existing
coordinator to validate and persist it. It does not invent subtasks, call a model,
dispatch work, approve actions, or execute tools.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from automation.agent_coordinator import (
    AgentPlan,
    SpecialistAgentCoordinator,
    SubtaskSpec,
)
from automation.capability_discovery import discover_capabilities


@dataclass(frozen=True, slots=True)
class TaskRequest:
    """Owner-reviewed task goal and explicit bounded decomposition."""

    goal: str
    idempotency_key: str
    subtasks: tuple[SubtaskSpec, ...]


def recommend_capabilities(
    *,
    coordinator: SpecialistAgentCoordinator,
    actor_id: str,
    goal: str,
    limit: int = 10,
) -> tuple[dict[str, object], ...]:
    """Recommend candidate specialists from a goal; never create or dispatch work."""
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 2000 or "\x00" in goal:
        raise ValueError("goal must be non-empty bounded text.")
    return discover_capabilities(
        coordinator=coordinator,
        actor_id=actor_id,
        query=goal,
        limit=limit,
    )


def prepare_task_plan(
    *,
    coordinator: SpecialistAgentCoordinator,
    actor_id: str,
    request: TaskRequest,
) -> AgentPlan:
    """Validate and persist an explicit task plan without advancing its workers."""
    if not isinstance(coordinator, SpecialistAgentCoordinator):
        raise TypeError("coordinator must be a SpecialistAgentCoordinator.")
    if not isinstance(request, TaskRequest):
        raise TypeError("request must be a TaskRequest.")
    if not isinstance(request.goal, str) or not request.goal.strip():
        raise ValueError("task goal must be non-empty.")
    if len(request.goal) > 2000 or "\x00" in request.goal:
        raise ValueError("task goal exceeds the supported bounds.")
    if not isinstance(request.subtasks, tuple):
        raise ValueError("subtasks must be an immutable tuple.")
    if not request.subtasks:
        raise ValueError("at least one explicit subtask is required.")
    # Preserve the parent goal in every worker task so downstream specialists
    # receive the objective even though the coordinator stores subtask records.
    contextual = tuple(
        replace(item, description=f"Overall goal: {request.goal.strip()}\\nSubtask: {item.description}")
        for item in request.subtasks
    )
    if any(len(item.description) > 2000 for item in contextual):
        raise ValueError("goal plus subtask description exceeds the supported bounds.")
    # Coordinator remains the single authority for DAG, routing, permissions,
    # idempotency, and persistence validation. No advance/dispatch is called.
    return coordinator.submit(
        actor_id=actor_id,
        idempotency_key=request.idempotency_key,
        subtasks=contextual,
    )
