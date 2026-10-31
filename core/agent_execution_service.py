"""Unified service facade for planning, approval and specialist execution.

This composes existing guarded subsystems; it does not add a second task store,
permission model, worker loop, or approval mechanism.
"""
from __future__ import annotations

from typing import Any

from automation.agent_coordinator import AgentPlan, SpecialistAgentCoordinator
from automation.workflow_approval import WorkflowApprovalService
from automation.worker_supervisor import WorkerCycleReport, WorkerSupervisor
from core.model_agent_planner import ModelAgentPlanner


class AgentExecutionService:
    """Application boundary for submitting goals and supervising approved agent work."""

    def __init__(
        self,
        planner: ModelAgentPlanner,
        coordinator: SpecialistAgentCoordinator,
        approvals: WorkflowApprovalService,
        supervisor: WorkerSupervisor,
    ) -> None:
        if not isinstance(planner, ModelAgentPlanner):
            raise TypeError("planner must be a ModelAgentPlanner")
        if not isinstance(coordinator, SpecialistAgentCoordinator):
            raise TypeError("coordinator must be a SpecialistAgentCoordinator")
        if not isinstance(approvals, WorkflowApprovalService):
            raise TypeError("approvals must be a WorkflowApprovalService")
        if not isinstance(supervisor, WorkerSupervisor):
            raise TypeError("supervisor must be a WorkerSupervisor")
        if planner.coordinator is not coordinator:
            raise ValueError("planner and service must share the same coordinator")
        if supervisor.runtime is not coordinator.runtime:
            raise ValueError("supervisor and coordinator must share the same TaskRuntime")
        if approvals.store is not supervisor.runtime.store:
            raise ValueError("approval service and workers must share the same workflow store")
        self.planner = planner
        self.coordinator = coordinator
        self.approvals = approvals
        self.supervisor = supervisor

    def submit_goal(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
        goal: str,
    ) -> AgentPlan:
        """Plan a goal and durably dispatch only dependency-ready approval-gated tasks."""
        proposed = self.planner.plan(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            goal=goal,
        )
        return self.coordinator.get_plan(actor_id=actor_id, plan_id=proposed.plan_id)

    def get_plan(self, *, actor_id: str, plan_id: str) -> AgentPlan:
        """Read the current durable plan through coordinator authorization."""
        return self.coordinator.get_plan(actor_id=actor_id, plan_id=plan_id)

    def approve_subtask(
        self,
        *,
        actor_id: str,
        plan_id: str,
        subtask_id: str,
        request_context: Any,
        expected_task_version: int,
    ) -> AgentPlan:
        """Approve one planned task using a trusted request principal, then refresh routing."""
        plan = self.coordinator.get_plan(actor_id=actor_id, plan_id=plan_id)
        subtask = next((item for item in plan.subtasks if item.subtask_id == subtask_id), None)
        if subtask is None:
            raise KeyError("subtask not found in plan")
        if subtask.task_id is None:
            raise ValueError("subtask is not dependency-ready for approval")
        if not subtask.requires_approval:
            raise ValueError("subtask does not require approval")
        self.approvals.approve(
            subtask.task_id,
            request_context=request_context,
            expected_version=expected_task_version,
        )
        # Re-evaluate dependency gates after the approval state has been persisted.
        return self.coordinator.advance(actor_id=actor_id, plan_id=plan_id)

    def run_worker_cycle(self) -> WorkerCycleReport:
        """Execute one finite worker batch; only store-approved tasks can be claimed."""
        return self.supervisor.run_cycle()
