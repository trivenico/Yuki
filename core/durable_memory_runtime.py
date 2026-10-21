"""Durable approval boundary for local memory mutations."""
from __future__ import annotations

from dataclasses import dataclass
import secrets


@dataclass(frozen=True)
class MemoryMutationResult:
    ok: bool
    message: str
    data: object = None
    stage: str = "report"


class DurableMemoryRuntime:
    """Execute only allow-listed memory mutations through the durable coordinator."""

    ALLOWED = {"memory:add", "memory:delete", "memory:clear"}

    def __init__(self, *, memory, coordinator, actor: str, confirm, session_id=None):
        if memory is None or not callable(getattr(memory, "add", None)):
            raise TypeError("A memory store is required.")
        if coordinator is None or not callable(getattr(coordinator, "record", None)) or not callable(getattr(coordinator, "approve", None)):
            raise TypeError("A durable approval coordinator is required.")
        if not isinstance(actor, str) or not actor.strip() or not callable(confirm):
            raise ValueError("A trusted actor and confirmation callback are required.")
        self.memory = memory
        self.coordinator = coordinator
        self.actor = actor
        self.confirm = confirm
        self.session_id = session_id or secrets.token_urlsafe(24)

    def mutate(self, operation: str, payload: dict) -> MemoryMutationResult:
        if operation not in self.ALLOWED or not isinstance(payload, dict):
            return MemoryMutationResult(False, "Blocked: unsupported memory mutation.", stage="invalid_input")
        try:
            pending = self.coordinator.record(
                None, {"action": {"skill_id": operation, "arguments": payload}},
                actor=self.actor, session_id=self.session_id,
            )
            outcome = self.coordinator.approve(
                self, pending.pending_id, actor=self.actor, session_id=self.session_id,
                confirm=lambda action: self.confirm(action),
            )
        except Exception:
            return MemoryMutationResult(False, "Memory mutation failed safely; no success was confirmed.",
                                        stage="recovery_required")
        if outcome.status != "completed":
            return MemoryMutationResult(False, f"Memory mutation not completed ({outcome.status}).",
                                        outcome.result, outcome.status)
        result = outcome.result
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            return MemoryMutationResult(False, "Invalid memory mutation result contract.",
                                        stage="invalid_result_contract")
        return MemoryMutationResult(result["ok"], str(result.get("message", "")),
                                    result.get("data"), "report" if result["ok"] else "observe")

    def execute_approved_action(self, action: dict):
        """Called by ApprovalCoordinator using its persisted approved action snapshot."""
        if not isinstance(action, dict) or set(action) != {"skill_id", "arguments"}:
            raise ValueError("Malformed approved memory action.")
        operation, payload = action["skill_id"], action["arguments"]
        if operation not in self.ALLOWED or not isinstance(payload, dict):
            raise ValueError("Unsupported approved memory action.")
        if operation == "memory:add":
            if set(payload) != {"text"} or not isinstance(payload["text"], str):
                raise ValueError("Malformed memory add payload.")
            item = self.memory.add(payload["text"])
            return {"ok": True, "message": "Memory saved.", "data": {"id": item["id"]}}
        if operation == "memory:delete":
            if set(payload) != {"memory_id"} or not isinstance(payload["memory_id"], str):
                raise ValueError("Malformed memory delete payload.")
            deleted = self.memory.delete(payload["memory_id"])
            return {"ok": True, "message": "Memory deleted." if deleted else "Memory entry not found.",
                    "data": {"deleted": deleted}}
        if operation == "memory:clear":
            if payload:
                raise ValueError("Memory clear payload must be empty.")
            deleted = self.memory.clear()
            return {"ok": True, "message": "Memory cleared.", "data": {"deleted_count": deleted}}
        raise ValueError("Unsupported memory operation.")
