"""Translate cognitive proposals into the existing specialist coordinator.

This adapter persists proposals only. Dispatch remains a separate authorized step.
"""
from __future__ import annotations

from automation.agent_coordinator import AgentPlan, SpecialistAgentCoordinator, SubtaskSpec
from core.cognitive_context import CognitiveBrief
from core.planner import Planner


class CognitiveAgentAdapter:
    """Bridge validated cognitive DAGs to the existing coordinator contract."""

    def __init__(self, coordinator: SpecialistAgentCoordinator):
        if not isinstance(coordinator, SpecialistAgentCoordinator):
            raise TypeError("coordinator must be a SpecialistAgentCoordinator.")
        self.coordinator = coordinator

    @staticmethod
    def to_subtask_specs(brief: CognitiveBrief) -> tuple[SubtaskSpec, ...]:
        if not isinstance(brief, CognitiveBrief):
            raise TypeError("brief must be a CognitiveBrief.")
        tasks = Planner.validate_tasks(brief.specialist_tasks, max_steps=10)
        return tuple(
            SubtaskSpec(
                subtask_id=task.task_id,
                description=task.description,
                required_capabilities=task.required_capabilities,
                depends_on=task.depends_on,
                requires_approval=task.requires_approval,
                required_permissions=(),
            )
            for task in tasks
        )

    def submit_proposal(self, *, actor_id: str, idempotency_key: str, brief: CognitiveBrief) -> AgentPlan:
        """Persist and route a proposal; do not create worker tasks."""
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200:
            raise ValueError("verified actor_id is required.")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("idempotency_key must be non-empty bounded text.")
        return self.coordinator.submit(
            actor_id=actor_id.strip(),
            idempotency_key=idempotency_key.strip(),
            subtasks=self.to_subtask_specs(brief),
        )
