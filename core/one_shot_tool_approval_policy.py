"""Fail-closed adapter that consumes one exact-action grant before dispatch."""
from __future__ import annotations

import hashlib

from core.approval_audit import SQLiteApprovalAuditLog
from core.tool_action import ToolActionEnvelope
from core.tool_action_approvals import SQLiteToolApprovalStore


class OneShotToolApprovalPolicy:
    """Authorize one invocation using a pre-issued, owner-confirmed grant.

    The approval ID and target scope are trusted composition inputs, not model
    arguments. The durable audit append occurs after atomic grant consumption
    and before the guarded runtime invokes the handler. Audit failure denies
    execution (the consumed grant is intentionally not restored).
    """

    def __init__(self, store: SQLiteToolApprovalStore, *, approval_id: str,
                 target_scope: str, audit_log: SQLiteApprovalAuditLog):
        if not isinstance(store, SQLiteToolApprovalStore):
            raise TypeError("trusted SQLite tool approval store required")
        if not isinstance(audit_log, SQLiteApprovalAuditLog):
            raise TypeError("trusted SQLite approval audit log required")
        if not isinstance(approval_id, str) or not approval_id or len(approval_id) > 128:
            raise ValueError("approval_id must be non-empty bounded text")
        if not isinstance(target_scope, str) or not target_scope.strip() or len(target_scope) > 512:
            raise ValueError("target_scope must be non-empty bounded text")
        self._store = store
        self._approval_id = approval_id
        self._target_scope = target_scope
        self._audit_log = audit_log
        self._used = False

    def authorize_action(self, action: ToolActionEnvelope) -> bool:
        if self._used or not isinstance(action, ToolActionEnvelope):
            return False
        if not action.owner_id or not action.session_id:
            return False
        if not self._store.consume(
            self._approval_id, action, target_scope=self._target_scope
        ):
            return False
        # A failed durable audit is fail-closed. Do not resurrect the consumed
        # grant: doing so would make crash/retry behavior ambiguous.
        self._audit_log.append(
            event_type="approved",
            pending_id=self._approval_id,
            actor=action.owner_id,
            # The audit schema caps skill_id at 120 characters; hash the
            # complete tool name to avoid truncation collisions.
            skill_id="tool:" + hashlib.sha256(action.tool_name.encode("utf-8")).hexdigest(),
        )
        self._used = True
        return True
