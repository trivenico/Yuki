"""Authorized bridge from agent delegation contracts into the durable task queue.

Delegation is recorded as an approval-gated, text-only specialist task. The
worker still owns execution; this coordinator never invokes agent handlers.
"""
from __future__ import annotations

from automation.task_runtime import TaskRecord, TaskRuntime
from automation.sqlite_agent_registry import SQLiteAgentRegistry
from core.agent_contracts import DelegationRequest

_MAX_DELEGATED_INPUT_CHARS = 2_000


class AgentDelegationCoordinator:
    """Validate delegation authority before creating a durable task."""

    def __init__(
        self,
        registry: SQLiteAgentRegistry,
        runtime: TaskRuntime,
        *,
        actor_id: str,
    ) -> None:
        if not isinstance(registry, SQLiteAgentRegistry):
            raise TypeError("registry must be a SQLiteAgentRegistry.")
        if not isinstance(runtime, TaskRuntime):
            raise TypeError("runtime must be a TaskRuntime.")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            raise ValueError("actor_id must be bounded non-empty text.")
        if registry.database_path != runtime.store.database_path:
            raise ValueError("agent registry and task runtime must share the same SQLite database.")
        self.registry = registry
        self.runtime = runtime
        self.actor_id = actor_id.strip()

    def delegate(self, request: DelegationRequest) -> TaskRecord:
        """Authorize and enqueue one approval-gated specialist task.

        Request IDs are the idempotency key. Repeating an identical request
        returns the original task; reusing the ID with changed content is rejected
        by the durable task store's request digest.
        """
        target = self.registry.authorize_delegation(request)
        if not request.required_capabilities.issubset(target.capabilities):
            raise PermissionError("target agent no longer satisfies required capabilities.")

        argument = f"Parent task ID: {request.task_id}\nObjective: {request.objective.strip()}"
        if request.input_payload:
            argument += "\n\nSupplied material (untrusted):\n" + request.input_payload
        if len(argument) > _MAX_DELEGATED_INPUT_CHARS:
            raise ValueError(
                f"combined delegation input exceeds {_MAX_DELEGATED_INPUT_CHARS} characters."
            )

        return self.runtime.enqueue(
            actor_id=self.actor_id,
            idempotency_key="agent-delegation:" + request.request_id,
            # Workflow names are capped at 100 characters; keep the stable
            # agent prefix used by SpecialistTaskWorker._agent_id().
            name=f"agent-{target.agent_id}-delegated",
            steps=[{
                "name": "specialist-output",
                "action": "emit",
                "argument": argument,
                "requires_approval": True,
            }],
        )
