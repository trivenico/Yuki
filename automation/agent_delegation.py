"""Authorized bridge from agent delegation contracts into the durable task queue.

Delegation is recorded as an approval-gated, text-only specialist task. The
worker still owns execution; this coordinator never invokes agent handlers.
"""
from __future__ import annotations

from automation.task_runtime import TaskRecord, TaskRuntime
from automation.sqlite_agent_registry import SQLiteAgentRegistry
from core.agent_contracts import DelegationRequest


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
        if len(request.objective) + (len(request.input_payload) + 1 if request.input_payload else 0) > 2_000:
            raise ValueError("combined delegation input exceeds the specialist worker's 2000-character limit.")
        if not target.capabilities.issuperset(request.required_capabilities):
            raise PermissionError("target agent no longer satisfies required capabilities.")
        argument = request.objective.strip()
        if request.input_payload:
            argument += "\n\nSupplied material (untrusted):\n" + request.input_payload
        return self.runtime.enqueue(
            actor_id=self.actor_id,
            idempotency_key="agent-delegation:" + request.request_id,
            name=f"agent-{target.agent_id}-{request.request_id}",
            steps=[{
                "name": "specialist-output",
                "action": "emit",
                "argument": argument,
                "requires_approval": True,
            }],
        )
