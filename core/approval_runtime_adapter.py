"""Host adapter connecting guarded tool proposals to durable approvals.

The adapter accepts only host-created execution context and a JSON action
snapshot. It never executes tools or accepts an executor from the model.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.approval_flow import ApprovalCoordinator


class ApprovalRuntimeAdapter:
    """Adapt GuardedToolRuntime approval callbacks to ApprovalCoordinator."""

    def __init__(self, coordinator: ApprovalCoordinator):
        if not isinstance(coordinator, ApprovalCoordinator):
            raise TypeError("coordinator must be an ApprovalCoordinator")
        self._coordinator = coordinator

    def __call__(
        self,
        tool_name: str,
        action: dict[str, Any],
        execution_context: Mapping[str, str],
    ) -> str:
        if not isinstance(tool_name, str) or not tool_name.strip() or len(tool_name) > 128:
            raise ValueError("tool_name must be non-empty bounded text")
        required = {"actor", "session_id", "conversation_id", "request_id"}
        if (
            not isinstance(execution_context, Mapping)
            or set(execution_context) != required
            or any(
                not isinstance(value, str) or not value.strip() or len(value) > 256
                for value in execution_context.values()
            )
        ):
            raise ValueError("a complete authenticated execution context is required")
        if not isinstance(action, dict):
            raise ValueError("approval action must be an object")
        skill_id = action.get("skill_id")
        arguments = action.get("arguments")
        if (
            not isinstance(skill_id, str)
            or skill_id != tool_name
            or not isinstance(arguments, dict)
        ):
            raise ValueError("approval action must bind its skill_id to the invoked tool")
        outcome = self._coordinator.record(
            None,
            {"action": action},
            actor=execution_context["actor"],
            session_id=execution_context["session_id"],
        )
        if outcome.status != "pending_approval" or not isinstance(outcome.pending_id, str):
            raise RuntimeError("approval coordinator did not issue a pending request")
        return outcome.pending_id
