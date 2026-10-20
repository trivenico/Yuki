"""One-time human approval of immutable planner proposals."""

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass

from core.observability import log_event

_LOGGER = logging.getLogger("yuki.approval")


@dataclass(frozen=True)
class PendingApproval:
    pending_id: str


@dataclass(frozen=True)
class ApprovalResult:
    ok: bool
    status: str
    message: str
    data: object = None


class ApprovalCoordinator:
    def __init__(self, *, pending_store=None, audit_log=None, max_pending=128):
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096")
        self.pending_store, self.audit_log, self.max_pending = pending_store, audit_log, max_pending
        self._pending = {}

    @staticmethod
    def _identity(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("trusted actor and session are required")
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _audit(self, event, pending_id, action, actor, session_id):
        # Structured logs contain only a one-way pending-ID digest and stable
        # metadata. Never serialize actor/session identifiers or tool arguments.
        log_event(
            _LOGGER,
            logging.INFO,
            f"approval.{event}",
            component="approval_flow",
            task_id=hashlib.sha256(str(pending_id).encode("utf-8")).hexdigest()[:16],
            status=event,
            reason_code=str(action.get("skill_id", "unknown"))[:80],
        )
        if self.audit_log is not None:
            self.audit_log.record(
                event,
                pending_id=pending_id,
                skill_id=action["skill_id"],
                actor=actor,
                session_id=session_id,
                arguments=action["arguments"],
            )

    def record(self, planner, result, *, actor, session_id):
        if getattr(result, "ok", False) is not True or not isinstance(
            getattr(result, "action", None), dict
        ):
            raise ValueError("only a planner proposal can be recorded")
        actor_hash, session_hash = self._identity(actor), self._identity(session_id)
        action = json.loads(json.dumps(result.action, allow_nan=False))
        pending_id = secrets.token_urlsafe(24)
        if self.pending_store is not None:
            stored = self.pending_store.put(
                action["skill_id"], action["arguments"], action, actor=actor, session_id=session_id
            )
            pending_id = stored.pending_id
        else:
            if len(self._pending) >= self.max_pending:
                raise RuntimeError("Pending approval capacity reached.")
            self._pending[pending_id] = (action, actor_hash, session_hash)
        self._audit("requested", pending_id, action, actor, session_id)
        return PendingApproval(pending_id)

    def approve(self, planner, pending_id, *, actor, session_id, confirm):
        if not callable(confirm):
            return ApprovalResult(False, "approval_unavailable", "Human approval is unavailable.")
        try:
            self._identity(actor)
            self._identity(session_id)
            if self.pending_store is not None:
                stored = self.pending_store.consume(pending_id, actor=actor, session_id=session_id)
                if stored is None:
                    return ApprovalResult(False, "pending_not_found", "Pending approval not found.")
                action = stored.action
            else:
                item = self._pending.get(pending_id)
                if item is None:
                    return ApprovalResult(False, "pending_not_found", "Pending approval not found.")
                action, actor_hash, session_hash = item
                if actor_hash != self._identity(actor) or session_hash != self._identity(
                    session_id
                ):
                    return ApprovalResult(False, "pending_not_found", "Pending approval not found.")
                del self._pending[pending_id]
                action = json.loads(json.dumps(action, allow_nan=False))
            # Confirmation sees a detached copy; only the stored snapshot is executed.
            approved = confirm(json.loads(json.dumps(action, allow_nan=False))) is True
            if not approved:
                self._audit("denied", pending_id, action, actor, session_id)
                return ApprovalResult(False, "approval_denied", "Approval was denied.")
            self._audit("approved", pending_id, action, actor, session_id)
            dispatcher = getattr(planner, "dispatcher", None)
            if dispatcher is None:
                raise ValueError("planner has no execution dispatcher")
            result = dispatcher.execute_approved(json.loads(json.dumps(action, allow_nan=False)))
            if result.ok:
                self._audit("executed", pending_id, action, actor, session_id)
                return ApprovalResult(True, "completed", result.message, result.data)
            self._audit("failed", pending_id, action, actor, session_id)
            return ApprovalResult(False, "execution_failed", result.message)
        except Exception:
            return ApprovalResult(False, "approval_failed", "Approval or execution failed safely.")
