"""Approval coordinator for exact pending manifest actions.

This is a CLI/service primitive, not an authenticated network API. Actor/session
must come from trusted identity, never from model output or arbitrary request data.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import secrets
import threading
from typing import Any, Callable

from core.approval_audit import SQLiteApprovalAuditLog
from core.manifest_task_agent import ManifestTaskAgent, PlannedTaskResult
from core.pending_approvals import SQLitePendingApprovalStore


@dataclass(frozen=True)
class PendingApproval:
    pending_id: str
    skill_id: str
    arguments: dict[str, Any]
    action: dict[str, Any]


class ApprovalCoordinator:
    """Stores exact pending snapshots, audits decisions, and consumes requests once."""

    def __init__(self, *, max_pending: int = 128,
                 pending_store: SQLitePendingApprovalStore | None = None,
                 audit_log: SQLiteApprovalAuditLog | None = None):
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096.")
        self.max_pending = max_pending
        self.pending_store = pending_store
        self.audit_log = audit_log
        self._lock = threading.Lock()
        self._pending: dict[str, PendingApproval] = {}

    def _audit(self, *, event_type: str, pending: PendingApproval,
               outcome: str, actor: str | None = None, session_id: str | None = None,
               details: dict[str, Any] | None = None) -> bool:
        if self.audit_log is None:
            return True
        try:
            self.audit_log.record(
                event_type=event_type, skill_id=pending.skill_id, action=pending.action,
                outcome=outcome, actor=actor, session_id=session_id,
                pending_id=pending.pending_id, details=details,
            )
            return True
        except Exception:
            return False

    def record(self, planner: ManifestTaskAgent, result: PlannedTaskResult, *, actor: str, session_id: str) -> PendingApproval:
        if result.status != "approval_required" or not result.pending_skill:
            raise ValueError("Only a task result awaiting approval can be recorded.")
        if (not isinstance(actor, str) or not actor.strip()
                or not isinstance(session_id, str) or not session_id.strip()):
            raise ValueError("Trusted actor and session identity are required.")
        if not isinstance(result.pending_arguments, dict):
            raise ValueError("Pending action arguments must be an object.")
        action = planner.dispatcher.approval_preview(result.pending_skill, result.pending_arguments)
        arguments = json.loads(json.dumps(result.pending_arguments, ensure_ascii=False, allow_nan=False))
        action_snapshot = json.loads(json.dumps(action, ensure_ascii=False, allow_nan=False))
        if self.pending_store is not None:
            stored = self.pending_store.put(result.pending_skill, arguments, action_snapshot, actor=actor, session_id=session_id)
            pending = PendingApproval(stored.pending_id, stored.skill_id, stored.arguments, stored.action)
        else:
            pending = PendingApproval(secrets.token_urlsafe(24), result.pending_skill, arguments, action_snapshot)
            with self._lock:
                if len(self._pending) >= self.max_pending:
                    raise RuntimeError("Pending approval capacity reached.")
                self._pending[pending.pending_id] = pending
        if not self._audit(event_type="requested", pending=pending, outcome="pending", actor=actor, session_id=session_id):
            self.deny(pending.pending_id)
            raise RuntimeError("Approval audit unavailable; action was not queued.")
        return pending

    def approve(self, planner: ManifestTaskAgent, pending_id: str, *,
                actor: str, session_id: str,
                confirm: Callable[[dict[str, Any]], bool]) -> PlannedTaskResult:
        if not callable(confirm):
            return PlannedTaskResult(False, "invalid_approval", "Approval callback is required.")
        if (not isinstance(actor, str) or not actor.strip()
                or not isinstance(session_id, str) or not session_id.strip()):
            return PlannedTaskResult(False, "invalid_approval", "Trusted actor/session context is required.")
        pending = self._get(pending_id, actor=actor, session_id=session_id)
        if pending is None:
            return PlannedTaskResult(False, "pending_not_found", "Pending action is missing or already consumed.")
        shown_action = json.loads(json.dumps(pending.action, ensure_ascii=False))
        try:
            approved = confirm(shown_action) is True
        except Exception:
            approved = False
        if not approved:
            if not self._audit(event_type="denied", pending=pending, outcome="denied",
                               actor=actor, session_id=session_id):
                self.deny(pending_id)
                return PlannedTaskResult(False, "approval_audit_failed", "Audit persistence failed; action was not executed.")
            self.deny(pending_id)
            return PlannedTaskResult(False, "approval_denied", "Action was not approved.")

        current = self._consume(pending_id, actor=actor, session_id=session_id)
        if current is None:
            return PlannedTaskResult(False, "pending_not_found", "Pending action was already consumed.")
        # Fail closed if the approval decision cannot be durably recorded.
        if not self._audit(event_type="approved", pending=current, outcome="approved",
                           actor=actor, session_id=session_id):
            return PlannedTaskResult(False, "approval_audit_failed",
                                     "Audit persistence failed; action was not executed.")
        try:
            token = planner.dispatcher.issue_approval(
                current.skill_id, current.arguments,
                actor=actor, session_id=session_id, approved=True,
            )
        except Exception:
            self._audit(event_type="failed", pending=current, outcome="failure",
                        actor=actor, session_id=session_id, details={"stage": "token_issue"})
            return PlannedTaskResult(False, "approval_issue_failed", "Could not issue action approval.")
        result = planner.resume_approved(
            current.skill_id, current.arguments, approval_token=token.token,
            actor=actor, session_id=session_id,
        )
        if not self._audit(event_type="executed" if result.ok else "failed", pending=current,
                           outcome="success" if result.ok else "failure", actor=actor,
                           session_id=session_id, details={"status": result.status, "steps": result.steps}):
            # The side effect may already have occurred; never claim it was rolled back.
            return PlannedTaskResult(result.ok, "audit_write_failed",
                                     "Action result is available, but its final audit event could not be persisted.",
                                     result.steps)
        return result

    def _get(self, pending_id: str, *, actor: str, session_id: str) -> PendingApproval | None:
        if self.pending_store is not None:
            stored = self.pending_store.get(pending_id)
            if stored is not None and (stored.actor_hash != self.pending_store._identity_hash(actor)
                                       or stored.session_hash != self.pending_store._identity_hash(session_id)):
                return None
            return (PendingApproval(stored.pending_id, stored.skill_id, stored.arguments, stored.action)
                    if stored is not None else None)
        with self._lock:
            return self._pending.get(pending_id)

    def _consume(self, pending_id: str, *, actor: str, session_id: str) -> PendingApproval | None:
        if self.pending_store is not None:
            stored = self.pending_store.consume(pending_id, actor=actor, session_id=session_id)
            return (PendingApproval(stored.pending_id, stored.skill_id, stored.arguments, stored.action)
                    if stored is not None else None)
        with self._lock:
            return self._pending.pop(pending_id, None)

    def deny(self, pending_id: str) -> bool:
        if self.pending_store is not None:
            return self.pending_store.deny(pending_id)
        with self._lock:
            return self._pending.pop(pending_id, None) is not None
