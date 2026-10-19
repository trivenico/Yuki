"""Human approval coordinator bound to immutable action snapshots and identity."""
from dataclasses import dataclass
import hashlib
import json
from core.action_approvals import ActionApprovalStore
from core.pending_approvals import SQLitePendingApprovalStore

@dataclass(frozen=True)
class ApprovalOutcome:
    ok: bool
    status: str
    pending_id: str | None = None
    result: object = None

class ApprovalCoordinator:
    def __init__(self, *, pending_store=None, audit_log=None, max_pending=128):
        if type(max_pending) is not int or not 1 <= max_pending <= 4096: raise ValueError("max_pending must be 1..4096")
        self.pending_store=pending_store
        self.audit_log=audit_log
        self.max_pending=max_pending
        self._pending=ActionApprovalStore(max_pending=max_pending)
        self._local_meta={}
    @staticmethod
    def _hash(value): return hashlib.sha256(value.encode("utf-8")).hexdigest()
    def _audit(self, kind, pending_id, skill_id, actor, session_id, outcome="ok"):
        if self.audit_log:
            self.audit_log.record(kind, pending_id=pending_id, skill_id=skill_id, actor_hash=self._hash(actor), session_hash=self._hash(session_id), outcome=outcome)
    def record(self, planner, result, *, actor, session_id):
        if not isinstance(actor,str) or not actor.strip() or not isinstance(session_id,str) or not session_id.strip(): raise ValueError("trusted actor and session required")
        if getattr(result,"status",None) != "approval_required" or not getattr(result,"skill_id",None): raise ValueError("planner result is not an approval request")
        action=planner.dispatcher.describe(result.skill_id, result.arguments)
        if self.pending_store:
            stored=self.pending_store.put(result.skill_id, result.arguments, action, actor=actor, session_id=session_id)
            pending_id=stored.pending_id
        else:
            item=self._pending.create(result.skill_id, result.arguments); pending_id=item.approval_id
            self._local_meta[pending_id]=(action, actor, session_id, result.skill_id)
        self._audit("requested",pending_id,result.skill_id,actor,session_id)
        return ApprovalOutcome(False,"pending",pending_id)
    def approve(self, planner, pending_id, *, actor, session_id, confirm):
        if self.pending_store:
            item=self.pending_store.get(pending_id)
            if item is None or item.actor_hash != self._hash(actor) or item.session_hash != self._hash(session_id): return ApprovalOutcome(False,"pending_not_found",pending_id)
            # Atomically consume before asking/dispatching to prevent concurrent replay.
            item=self.pending_store.consume(pending_id,actor=actor,session_id=session_id)
            if item is None: return ApprovalOutcome(False,"pending_not_found",pending_id)
            action=item.action; skill_id=item.skill_id; arguments=item.arguments
        else:
            meta=self._local_meta.pop(pending_id,None)
            item=self._pending.consume(pending_id)
            if item is None or meta is None or meta[1] != actor or meta[2] != session_id: return ApprovalOutcome(False,"pending_not_found",pending_id)
            action,_,_,skill_id=meta; arguments=item.arguments
        try: visible=json.loads(json.dumps(action,ensure_ascii=False,allow_nan=False))
        except Exception: return ApprovalOutcome(False,"invalid_snapshot",pending_id)
        if not callable(confirm) or confirm(visible) is not True:
            self._audit("denied",pending_id,skill_id,actor,session_id,"declined")
            return ApprovalOutcome(False,"approval_denied",pending_id)
        self._audit("approved",pending_id,skill_id,actor,session_id)
        # Use stored action arguments, never the mutable confirmation copy.
        dispatch=planner.dispatcher.dispatch(skill_id,arguments,approved=True)
        if dispatch.ok:
            self._audit("executed",pending_id,skill_id,actor,session_id)
            return ApprovalOutcome(True,"completed",pending_id,dispatch.result)
        self._audit("failed",pending_id,skill_id,actor,session_id,dispatch.status)
        return ApprovalOutcome(False,dispatch.status,pending_id)
