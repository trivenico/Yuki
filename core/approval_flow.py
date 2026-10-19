"""Human approval coordinator with immutable action snapshots and one-time claims."""
from __future__ import annotations
from dataclasses import dataclass
import copy
import hashlib
import json
from typing import Any, Callable
from core.approval_audit import SQLiteApprovalAuditLog
from core.pending_approvals import SQLitePendingApprovalStore

@dataclass(frozen=True)
class ApprovalRequest:
    pending_id: str
    skill_id: str
    arguments: dict[str, Any]
    expires_at: str

@dataclass(frozen=True)
class ApprovalResult:
    ok: bool
    status: str
    message: str = ""
    output: dict[str, Any] | None = None

def _hash_identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

class ApprovalCoordinator:
    def __init__(self, *, pending_store: SQLitePendingApprovalStore | None = None,
                 audit_log: SQLiteApprovalAuditLog | None = None, max_pending: int = 128):
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096.")
        self.pending_store = pending_store
        self.audit_log = audit_log
        self.max_pending = max_pending
        self._memory: dict[str, dict[str, Any]] = {}
        import threading
        self._lock = threading.RLock()

    def _audit(self, event: str, pending_id: str, actor: str, skill_id: str):
        if self.audit_log:
            self.audit_log.append(event, pending_id=pending_id, actor_hash=_hash_identity(actor),
                                  skill_id=skill_id, details={})

    def record(self, planner, result, *, actor: str, session_id: str) -> ApprovalRequest:
        if not isinstance(actor, str) or not actor.strip() or not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("Actor and session identity are required.")
        action = getattr(result, "action", None)
        if not getattr(result, "ok", False) or action is None:
            raise ValueError("Only a valid proposed action can be sent for approval.")
        skill_id = action.skill_id
        args = copy.deepcopy(action.arguments)
        # Validate against the registered manifest before recording the exact action shown to a human.
        registered = planner.dispatcher._registered.get(skill_id)
        if not registered or not registered[0].validate_object(registered[0].input_schema, args):
            raise ValueError("Proposed action failed manifest validation.")
        public_action = {"skill_id": skill_id, "arguments": args, "capabilities": list(registered[0].capabilities),
                         "risk": registered[0].risk}
        snapshot = json.loads(json.dumps(public_action, allow_nan=False, sort_keys=True))
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        with self._lock:
            if self.pending_store is None and len(self._memory) >= self.max_pending:
                raise RuntimeError("Pending approval capacity reached.")
            if self.pending_store:
                stored = self.pending_store.put(skill_id, args, {**snapshot, "action_hash": digest},
                                                actor=actor, session_id=session_id)
                pending_id, expires_at = stored.pending_id, stored.expires_at
            else:
                import secrets
                from datetime import datetime, timedelta, timezone
                pending_id = secrets.token_urlsafe(24)
                expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
                self._memory[pending_id] = {"action": snapshot, "hash": digest, "actor": actor,
                                            "session": session_id, "expires_at": expires_at}
        self._audit("requested", pending_id, actor, skill_id)
        return ApprovalRequest(pending_id, skill_id, copy.deepcopy(args), expires_at)

    def approve(self, planner, pending_id: str, *, actor: str, session_id: str,
                confirm: Callable[[dict[str, Any]], bool]) -> ApprovalResult:
        if not isinstance(confirm, Callable) or not isinstance(actor, str) or not actor.strip() or not isinstance(session_id, str) or not session_id.strip():
            return ApprovalResult(False, "invalid_request", "A trusted actor, session, and confirmation callback are required.")
        with self._lock:
            if self.pending_store:
                stored = self.pending_store.consume(pending_id, actor=actor, session_id=session_id)
                if stored is None:
                    return ApprovalResult(False, "pending_not_found", "Approval is missing, expired, or bound to another identity.")
                action = stored.action
                digest = action.pop("action_hash", None)
                action_for_display = {k: v for k, v in action.items() if k != "action_hash"}
                expected = hashlib.sha256(json.dumps(action_for_display, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
                if not isinstance(digest, str) or digest != expected:
                    self._audit("failed", pending_id, actor, stored.skill_id)
                    return ApprovalResult(False, "tampered", "Stored action integrity validation failed.")
            else:
                # Do not consume an approval on an identity mismatch; otherwise a caller
                # who guesses an ID could invalidate the legitimate owner's request.
                item = self._memory.get(pending_id)
                if not item or item["actor"] != actor or item["session"] != session_id:
                    return ApprovalResult(False, "pending_not_found", "Approval is missing or bound to another identity.")
                self._memory.pop(pending_id, None)
                from datetime import datetime, timezone
                if item["expires_at"] <= datetime.now(timezone.utc).isoformat():
                    self._audit("expired", pending_id, actor, item["action"]["skill_id"])
                    return ApprovalResult(False, "expired", "Approval expired.")
                action_for_display = copy.deepcopy(item["action"])
                expected = hashlib.sha256(json.dumps(action_for_display, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
                if expected != item["hash"]:
                    self._audit("failed", pending_id, actor, action_for_display["skill_id"])
                    return ApprovalResult(False, "tampered", "Stored action integrity validation failed.")
            shown = copy.deepcopy(action_for_display)
            try:
                decision = confirm(shown)
            except Exception:
                self._audit("denied", pending_id, actor, action_for_display["skill_id"])
                return ApprovalResult(False, "approval_denied", "Confirmation failed closed.")
            if decision is not True:
                self._audit("denied", pending_id, actor, action_for_display["skill_id"])
                return ApprovalResult(False, "approval_denied", "Human approval was not granted.")
            # Callback receives a detached copy; execute only the canonical stored snapshot.
            canonical = action_for_display
            skill_id, args = canonical["skill_id"], copy.deepcopy(canonical["arguments"])
            self._audit("approved", pending_id, actor, skill_id)
            try:
                output = planner.dispatcher.execute(skill_id, args, approved=True)
            except Exception:
                self._audit("failed", pending_id, actor, skill_id)
                return ApprovalResult(False, "execution_failed", "Approved action failed safely.")
            self._audit("executed", pending_id, actor, skill_id)
            return ApprovalResult(True, "completed", "Action completed.", output)
