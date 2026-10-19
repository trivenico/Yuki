"""Approval coordinator for human-in-the-loop action verification and execution."""
import copy
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class PendingItem:
    pending_id: str
    action: dict
    actor: str
    session_id: str


@dataclass
class ExecutionResult:
    ok: bool
    status: str
    result: Any = None


class ApprovalCoordinator:
    def __init__(self, pending_store=None, audit_log=None, max_pending: int = 128):
        self.pending_store = pending_store
        self.audit_log = audit_log
        self.max_pending = max_pending
        self._in_memory_pending = {}

    def record(self, planner, result, actor: str, session_id: str) -> PendingItem:
        if len(self._in_memory_pending) >= self.max_pending:
            raise RuntimeError("Pending capacity reached.")

        action = getattr(result, "action", {"skill_id": getattr(result, "skill_id", ""), "arguments": getattr(result, "arguments", {})})
        if self.pending_store:
            stored = self.pending_store.put(
                skill_id=action.get("skill_id", ""),
                arguments=action.get("arguments", {}),
                action=action,
                actor=actor,
                session_id=session_id,
            )
            pending_id = stored.pending_id
        else:
            pending_id = uuid.uuid4().hex[:16]
            self._in_memory_pending[pending_id] = PendingItem(pending_id, copy.deepcopy(action), actor, session_id)

        if self.audit_log:
            self.audit_log.record_event("requested", pending_id, actor, session_id)

        return PendingItem(pending_id, copy.deepcopy(action), actor, session_id)

    def approve(self, planner, pending_id: str, actor: str, session_id: str, confirm: Callable[[dict], bool]) -> ExecutionResult:
        if self.pending_store:
            item = self.pending_store.get(pending_id)
            if item is None:
                return ExecutionResult(ok=False, status="pending_not_found")
            # Verify actor & session match store
            consumed = self.pending_store.consume(pending_id, actor=actor, session_id=session_id)
            if consumed is None:
                return ExecutionResult(ok=False, status="pending_not_found")
            action_snapshot = consumed.action
        else:
            item = self._in_memory_pending.get(pending_id)
            if item is None:
                return ExecutionResult(ok=False, status="pending_not_found")
            if item.actor != actor or item.session_id != session_id:
                return ExecutionResult(ok=False, status="pending_not_found")
            del self._in_memory_pending[pending_id]
            action_snapshot = item.action

        confirm_action_copy = copy.deepcopy(action_snapshot)
        if not confirm(confirm_action_copy):
            if self.audit_log:
                self.audit_log.record_event("denied", pending_id, actor, session_id)
            return ExecutionResult(ok=False, status="approval_denied")

        if self.audit_log:
            self.audit_log.record_event("approved", pending_id, actor, session_id)

        res = planner.execute_proposed(action_snapshot)

        if self.audit_log:
            self.audit_log.record_event("executed", pending_id, actor, session_id)

        return ExecutionResult(ok=True, status="completed", result=res)
