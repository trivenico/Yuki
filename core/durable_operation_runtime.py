"""Durable approval wrapper for explicitly registered non-skill operations.

The operation registry is supplied by trusted application code. User/model input can
select only a registered operation and JSON-safe arguments; it cannot supply a
callable or import path. The coordinator persists approval/audit/ledger state before
the registered callable is invoked.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from core.approval_flow import ApprovalCoordinator, ApprovalOutcome


class DurableOperationRuntime:
    def __init__(
        self,
        *,
        coordinator: ApprovalCoordinator,
        operations: Mapping[str, Callable[..., Any]],
        actor: str,
        session_id: str,
        confirm: Callable[[dict], bool],
    ) -> None:
        if not isinstance(coordinator, ApprovalCoordinator):
            raise TypeError("A durable ApprovalCoordinator is required.")
        if not isinstance(operations, Mapping) or not operations:
            raise ValueError("At least one explicitly registered operation is required.")
        normalized = {}
        for name, callback in operations.items():
            if (
                not isinstance(name, str) or not name
                or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in name)
                or not callable(callback)
            ):
                raise ValueError("Operation names and handlers must be explicit and valid.")
            normalized[name] = callback
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("A trusted actor identity is required.")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("A session identity is required.")
        if not callable(confirm):
            raise ValueError("An explicit confirmation callback is required.")
        self.coordinator = coordinator
        self.operations = normalized
        self.actor = actor
        self.session_id = session_id
        self.confirm = confirm

    def execute(self, operation: str, arguments: dict[str, Any]) -> ApprovalOutcome:
        """Request approval and execute one registered operation at most once per key."""
        if operation not in self.operations:
            raise ValueError("Operation is not registered.")
        if not isinstance(arguments, dict):
            raise ValueError("Operation arguments must be a JSON object.")
        try:
            snapshot = json.loads(json.dumps(arguments, ensure_ascii=False, allow_nan=False))
            encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("Operation arguments must be JSON-safe.") from exc
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("Operation arguments exceed 64 KiB.")
        action = {
            "skill_id": "operation:" + operation,
            "arguments": snapshot,
            "operation": operation,
        }
        pending = self.coordinator.record(
            None, {"action": action}, actor=self.actor, session_id=self.session_id
        )
        if pending.status != "pending_approval" or not pending.pending_id:
            return pending
        return self.coordinator.approve(
            self, pending.pending_id, actor=self.actor, session_id=self.session_id,
            confirm=self.confirm,
        )

    def execute_approved_action(self, action: dict[str, Any]) -> Any:
        """Coordinator-only executor for a frozen, approved action snapshot."""
        if not isinstance(action, dict):
            raise ValueError("Approved operation action must be an object.")
        operation = action.get("operation")
        if not isinstance(operation, str) or operation not in self.operations:
            raise ValueError("Approved operation is not registered.")
        skill_id = action.get("skill_id")
        if skill_id != "operation:" + operation:
            raise ValueError("Operation identity does not match the approved action.")
        arguments = action.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError("Approved operation arguments must be an object.")
        # Only keyword arguments from the stored JSON snapshot are passed to the
        # trusted handler; no dynamic imports or user-supplied callable names.
        return self.operations[operation](**arguments)
