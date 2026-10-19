"""Human-in-the-loop approval coordinator for immutable, one-time action proposals."""
import copy
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from core.action_approvals import action_fingerprint


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
        if not isinstance(max_pending, int) or isinstance(max_pending, bool) or not 1 <= max_pending <= 1000:
            raise ValueError("max_pending must be between 1 and 1000.")
        self.pending_store = pending_store
        self.audit_log = audit_log
        self.max_pending = max_pending
        self._in_memory_pending = {}

    def record(self, planner, result, actor: str, session_id: str) -> PendingItem:
        if not isinstance(actor, str) or not actor or not isinstance(session_id, str) or not session_id:
            raise ValueError("Actor and session are required.")
        if len(self._in_memory_pending) >= self.max_pending:
            raise RuntimeError("Pending capacity reached.")
        action = getattr(result, "action", {
            "skill_id": getattr(result, "skill_id", ""),
            "arguments": getattr(result, "arguments", {}),
        })
        if not isinstance(action, dict) or not isinstance(action.get("skill_id"), str) or not action["skill_id"]:
            raise ValueError("Invalid proposed action.")
        if not isinstance(action.get("arguments"), dict):
            raise ValueError("Proposed action arguments must be an object.")
        # Freeze the action at proposal time; later mutation of model/result objects
        # must not alter what the user is asked to approve.
        snapshot = copy.deepcopy(action)
        action_fingerprint(snapshot)
        if self.pending_store:
            stored = self.pending_store.put(
                skill_id=snapshot["skill_id"],
                arguments=snapshot["arguments"],
                action=snapshot,
                actor=actor,
                session_id=session_id,
            )
            pending_id = stored.pending_id
        else:
            pending_id = uuid.uuid4().hex
            self._in_memory_pending[pending_id] = PendingItem(pending_id, snapshot, actor, session_id)
        if self.audit_log:
            self.audit_log.record_event("requested", pending_id, actor, session_id)
        return PendingItem(pending_id, copy.deepcopy(snapshot), actor, session_id)

    def approve(self, planner, pending_id: str, actor: str, session_id: str, confirm: Callable[[dict], bool] = None) -> ExecutionResult:
        # No confirmation callback is an explicit denial, never an implicit approval.
        if not callable(confirm):
            return ExecutionResult(ok=False, status="approval_unavailable")
        if self.pending_store:
            item = self.pending_store.get(pending_id)
            if item is None:
                return ExecutionResult(ok=False, status="pending_not_found")
            # The persistent store verifies hashed actor/session bindings atomically.
            consumed = self.pending_store.consume(pending_id, actor=actor, session_id=session_id)
            if consumed is None:
                return ExecutionResult(ok=False, status="pending_not_found")
            action_snapshot = copy.deepcopy(consumed.action)
        else:
            item = self._in_memory_pending.get(pending_id)
            if item is None or item.actor != actor or item.session_id != session_id:
                return ExecutionResult(ok=False, status="pending_not_found")
            del self._in_memory_pending[pending_id]
            action_snapshot = copy.deepcopy(item.action)

        dispatcher = getattr(planner, "dispatcher", None)
        approval_store = getattr(dispatcher, "approval_store", None)
        permission_gate = getattr(dispatcher, "permission_gate", None)
        registered = getattr(dispatcher, "registered", {})
        entry = registered.get(action_snapshot.get("skill_id")) if isinstance(registered, dict) else None
        manifest = entry.get("manifest") if isinstance(entry, dict) else None
        if approval_store is None or permission_gate is None or manifest is None:
            if self.audit_log:
                self.audit_log.record_event("execution_blocked", pending_id, actor, session_id)
            return ExecutionResult(ok=False, status="approval_unavailable")

        # One human confirmation produces a receipt bound to the immutable action.
        # Dispatcher consumes this receipt, avoiding a second prompt while preserving
        # its fail-closed PermissionGate and exact-action checks.
        receipt = permission_gate.issue_receipt(
            action_snapshot, confirm, risk=manifest.risk
        )
        if receipt is None:
            if self.audit_log:
                self.audit_log.record_event("denied", pending_id, actor, session_id)
            return ExecutionResult(ok=False, status="approval_denied")

        fingerprint = action_fingerprint(action_snapshot)
        if approval_store.approve(fingerprint, True) is not True:
            return ExecutionResult(ok=False, status="approval_grant_failed")
        if self.audit_log:
            self.audit_log.record_event("approved", pending_id, actor, session_id)
        try:
            result = planner.execute_proposed(
                copy.deepcopy(action_snapshot), approval_receipt=receipt
            )
        except Exception:
            if self.audit_log:
                self.audit_log.record_event("execution_failed", pending_id, actor, session_id)
            return ExecutionResult(ok=False, status="execution_failed")
        if self.audit_log:
            self.audit_log.record_event("executed", pending_id, actor, session_id)
        return ExecutionResult(ok=True, status="completed", result=result)
