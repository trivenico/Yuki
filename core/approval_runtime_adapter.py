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
        if coordinator.pending_store is None or not callable(
            getattr(coordinator.pending_store, "put", None)
        ):
            raise ValueError("a durable pending approval store is required")
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
        bound_action = dict(action)
        declared_tool = bound_action.get("tool_name")
        if (
            not isinstance(skill_id, str)
            or not skill_id.strip()
            or len(skill_id) > 120
            or not isinstance(arguments, dict)
            or (declared_tool is not None and declared_tool != tool_name)
        ):
            raise ValueError("approval action must contain a valid skill and arguments")
        # Persist the actual guarded registry name as part of the immutable
        # approval snapshot. Skill identifiers and tool names are distinct
        # namespaces and must not be assumed to be equal.
        bound_action["tool_name"] = tool_name
        import json
        request_key = json.dumps(
            [execution_context["conversation_id"], execution_context["request_id"]],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        outcome = self._coordinator.record(
            None,
            {"action": bound_action},
            actor=execution_context["actor"],
            session_id=execution_context["session_id"],
            idempotency_key=request_key,
        )
        if outcome.status != "pending_approval" or not isinstance(outcome.pending_id, str):
            raise RuntimeError("approval coordinator did not issue a pending request")
        return outcome.pending_id
