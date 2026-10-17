"""In-process approval coordinator for exact pending manifest actions.

This is a CLI/service primitive, not an authenticated network approval API.
Callers must derive actor/session from trusted identity, not user-supplied request fields.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import secrets
import threading
from typing import Any, Callable

from core.manifest_task_agent import ManifestTaskAgent, PlannedTaskResult


@dataclass(frozen=True)
class PendingApproval:
    pending_id: str
    skill_id: str
    arguments: dict[str, Any]
    action: dict[str, Any]


class ApprovalCoordinator:
    """Stores pending actions server-side and resumes only the stored snapshot."""

    def __init__(self, *, max_pending: int = 128):
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096.")
        self.max_pending = max_pending
        self._lock = threading.Lock()
        self._pending: dict[str, PendingApproval] = {}

    def record(self, planner: ManifestTaskAgent, result: PlannedTaskResult) -> PendingApproval:
        if result.status != "approval_required" or not result.pending_skill:
            raise ValueError("Only a task result awaiting approval can be recorded.")
        if not isinstance(result.pending_arguments, dict):
            raise ValueError("Pending action arguments must be an object.")
        action = planner.dispatcher.approval_preview(
            result.pending_skill, result.pending_arguments
        )
        # JSON round-trip produces a detached, finite JSON snapshot.
        arguments = json.loads(json.dumps(result.pending_arguments, ensure_ascii=False, allow_nan=False))
        action_snapshot = json.loads(json.dumps(action, ensure_ascii=False, allow_nan=False))
        pending = PendingApproval(
            pending_id=secrets.token_urlsafe(24),
            skill_id=result.pending_skill,
            arguments=arguments,
            action=action_snapshot,
        )
        with self._lock:
            if len(self._pending) >= self.max_pending:
                raise RuntimeError("Pending approval capacity reached.")
            self._pending[pending.pending_id] = pending
        return pending

    def approve(self, planner: ManifestTaskAgent, pending_id: str, *,
                actor: str, session_id: str,
                confirm: Callable[[dict[str, Any]], bool]) -> PlannedTaskResult:
        """Display the stored exact action, then issue/consume a one-time approval."""
        if not callable(confirm):
            return PlannedTaskResult(False, "invalid_approval", "Approval callback is required.")
        if not isinstance(actor, str) or not actor.strip() or not isinstance(session_id, str) or not session_id.strip():
            return PlannedTaskResult(False, "invalid_approval", "Trusted actor/session context is required.")
        with self._lock:
            pending = self._pending.get(pending_id)
        if pending is None:
            return PlannedTaskResult(False, "pending_not_found", "Pending action is missing or already consumed.")
        # Confirm is given a detached copy so a UI callback cannot mutate the stored action.
        shown_action = json.loads(json.dumps(pending.action, ensure_ascii=False))
        try:
            approved = confirm(shown_action) is True
        except Exception:
            approved = False
        if not approved:
            with self._lock:
                self._pending.pop(pending_id, None)
            return PlannedTaskResult(False, "approval_denied", "Action was not approved.")
        # Consume pending ID before executing, preventing concurrent/replayed approvals.
        with self._lock:
            current = self._pending.pop(pending_id, None)
        if current is None:
            return PlannedTaskResult(False, "pending_not_found", "Pending action was already consumed.")
        try:
            token = planner.dispatcher.issue_approval(
                current.skill_id, current.arguments,
                actor=actor, session_id=session_id, approved=True,
            )
        except Exception:
            return PlannedTaskResult(False, "approval_issue_failed", "Could not issue action approval.")
        return planner.resume_approved(
            current.skill_id, current.arguments,
            approval_token=token.token, actor=actor, session_id=session_id,
        )

    def deny(self, pending_id: str) -> bool:
        with self._lock:
            return self._pending.pop(pending_id, None) is not None
