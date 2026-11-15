"""Server-side execution of explicitly approved, persisted tool proposals.

The executor never accepts model-authored arguments. It reconstructs the trusted
action context from the owner-scoped durable proposal, claims it once, issues a
single-use token, and delegates authorization/journaling to GuardedToolRuntime.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json

from core.pending_tool_action_proposals import SQLitePendingToolActionProposalStore
from core.tool_action_approvals import SQLiteToolActionApprovalStore
from core.tool_action_context import ToolActionContext


@dataclass(frozen=True)
class ApprovedToolActionExecution:
    proposal_id: str
    state: str
    result: str | None


class ApprovedToolActionExecutor:
    """Execute an approved proposal once through the guarded tool runtime."""

    def __init__(self, proposals, approvals, guarded_runtime):
        if not isinstance(proposals, SQLitePendingToolActionProposalStore):
            raise TypeError("proposals must be a SQLitePendingToolActionProposalStore")
        if not isinstance(approvals, SQLiteToolActionApprovalStore):
            raise TypeError("approvals must be a SQLiteToolActionApprovalStore")
        if not callable(getattr(guarded_runtime, "invoke", None)):
            raise TypeError("guarded_runtime must provide invoke()")
        if not callable(getattr(guarded_runtime, "requires_user_confirmation", None)):
            raise TypeError("guarded_runtime must classify confirmation-required tools")
        self.proposals = proposals
        self.approvals = approvals
        self.guarded_runtime = guarded_runtime

    def recover_interrupted_execution(
        self, proposal_id: str, *, principal_id: str,
    ) -> ApprovedToolActionExecution:
        """Reconcile a stranded proposal claim without ever redispatching its tool."""
        proposal = self.proposals.get(proposal_id, principal_id=principal_id)
        if proposal is None:
            raise LookupError("approved proposal was not found")
        if proposal.execution_state != "executing":
            return ApprovedToolActionExecution(
                proposal_id=proposal_id,
                state=proposal.execution_state,
                result=None,
            )
        journal = getattr(self.guarded_runtime, "_action_journal", None)
        if journal is None:
            self.proposals.recover_execution_state(
                proposal_id, principal_id=principal_id, state="unknown",
            )
            return ApprovedToolActionExecution(proposal_id, state="unknown", result=None)
        # Recover only this proposal's action; an owner must not trigger
        # maintenance transitions for another user's unrelated executions.
        action = journal.recover_expired_execution(proposal.idempotency_key)
        if action is not None and action.state == "executing":
            return ApprovedToolActionExecution(proposal_id, state="executing", result=None)
        recovered_state = "finished" if action is not None and action.state == "succeeded" else "unknown"
        self.proposals.recover_execution_state(
            proposal_id, principal_id=principal_id, state=recovered_state,
        )
        return ApprovedToolActionExecution(
            proposal_id=proposal_id,
            state=recovered_state,
            result=str(action.result) if recovered_state == "finished" else None,
        )

    def execute(self, proposal_id: str, *, principal_id: str) -> ApprovedToolActionExecution:
        """Execute only the exact owner-approved proposal; never accept replacement args."""
        proposal = self.proposals.get(proposal_id, principal_id=principal_id)
        if proposal is None:
            raise LookupError("approved proposal was not found")
        if proposal.status != "approved":
            raise ValueError("proposal has not been approved")
        if datetime.now(timezone.utc) >= datetime.fromisoformat(proposal.expires_at):
            raise ValueError("approved proposal has expired")
        if self.guarded_runtime.requires_user_confirmation(proposal.tool_name) is not True:
            raise ValueError("tool is no longer classified as a permitted confirmation-required action")
        context = ToolActionContext(
            action_id=proposal.action_id,
            idempotency_key=proposal.idempotency_key,
            principal_id=proposal.principal_id,
            tool_name=proposal.tool_name,
            request_digest=proposal.request_digest,
        )
        context.verify(principal_id, proposal.tool_name, proposal.arguments)
        if not self.proposals.claim_execution(proposal_id, principal_id=principal_id):
            raise ValueError("proposal execution was already claimed or is not approved")
        try:
            approval_id = self.approvals.issue(context, principal_id=principal_id)
        except Exception:
            self.proposals.finish_execution(
                proposal_id, principal_id=principal_id, outcome_unknown=True,
            )
            raise
        try:
            argument = json.dumps(
                proposal.arguments, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            )
            result = self.guarded_runtime.invoke(
                proposal.tool_name, argument, action_context=context,
                principal_id=principal_id, user_confirmed=True,
                approval_id=approval_id,
            )
        except Exception:
            self.proposals.finish_execution(
                proposal_id, principal_id=principal_id, outcome_unknown=True,
            )
            raise
        journal = getattr(self.guarded_runtime, "_action_journal", None)
        action = journal.get(proposal.idempotency_key) if journal is not None else None
        # A textual tool response is not proof of success. Only a durable
        # succeeded journal record confirms that the side effect completed.
        succeeded = action is not None and action.state == "succeeded"
        self.proposals.finish_execution(
            proposal_id, principal_id=principal_id, outcome_unknown=not succeeded,
        )
        return ApprovedToolActionExecution(
            proposal_id=proposal_id,
            state="finished" if succeeded else "unknown",
            result=result,
        )
