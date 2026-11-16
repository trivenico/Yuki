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
from core.guarded_tool_runtime import GuardedToolRuntime
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
        if not isinstance(guarded_runtime, GuardedToolRuntime):
            raise TypeError("guarded_runtime must be a GuardedToolRuntime")
        if guarded_runtime._approval_store is not approvals:
            raise ValueError("executor and guarded runtime must share the exact approval store")
        if guarded_runtime._action_journal is None:
            raise ValueError("approved execution requires a durable action journal")
        if guarded_runtime._action_journal.database_path != approvals.database_path:
            raise ValueError("approval store and guarded action journal must share one SQLite database")
        if proposals.database_path != approvals.database_path:
            raise ValueError("proposal store, approval store, and guarded action journal must share one SQLite database")
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
            # No journal means the executor may still be between its proposal
            # claim and durable action reservation. Do not race it by declaring
            # the claim abandoned.
            return ApprovedToolActionExecution(proposal_id, state="executing", result=None)
        # Recover only this proposal's action; an owner must not trigger
        # maintenance transitions for another user's unrelated executions.
        action = journal.recover_expired_execution(proposal.idempotency_key)
        if action is None or action.state == "executing":
            # No journal row means the worker may still be between proposal
            # claim and reservation. An active execution lease is also live.
            return ApprovedToolActionExecution(proposal_id, state="executing", result=None)
        if action.state == "prepared":
            # A prepared reservation proves the proposal claim was committed
            # atomically, but it does not prove the worker has stopped. The live
            # executor may still be issuing its approval token before dispatch.
            # Releasing here could let a second worker dispatch the same action.
            # Only the worker that observes its own pre-dispatch failure may release
            # this claim; crash recovery must leave it fenced for operator review.
            return ApprovedToolActionExecution(proposal_id, state="executing", result=None)
        recovered_state = "finished" if action.state == "succeeded" else "unknown"
        self.proposals.recover_execution_state(
            proposal_id, principal_id=principal_id, state=recovered_state,
        )
        return ApprovedToolActionExecution(
            proposal_id=proposal_id,
            state=recovered_state,
            result=str(action.result) if recovered_state == "finished" else None,
        )

    def _release_pre_dispatch_claim(
        self, proposal, *, principal_id: str,
    ) -> bool:
        """Release only when the durable journal proves dispatch never began."""
        journal = self.guarded_runtime._action_journal
        action = journal.get(proposal.idempotency_key)
        if action is not None and not (
            action.state == "prepared" and (action.version >= 1)
        ):
            return False
        return self.proposals._release_recovered_pre_dispatch_claim(
            proposal.proposal_id, principal_id=principal_id,
        )

    def _claim_proposal_and_reserve_action(self, proposal, *, principal_id: str) -> bool:
        """Atomically claim the proposal and persist its prepared journal row."""
        journal = self.guarded_runtime._action_journal
        now = self.proposals._now().isoformat()
        with self.proposals._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = db.execute(
                    """UPDATE pending_tool_action_proposals
                       SET execution_state='executing'
                       WHERE proposal_id=? AND principal_id=?
                         AND status='approved' AND execution_state='not_started'
                         AND expires_at>?""",
                    (proposal.proposal_id, principal_id, now),
                ).rowcount
                if changed != 1:
                    db.execute("ROLLBACK")
                    return False
                journal.reserve_in_transaction(
                    db, proposal.idempotency_key, proposal.tool_name,
                    proposal.arguments, action_id=proposal.action_id,
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return True

    def execute(self, proposal_id: str, *, principal_id: str) -> ApprovedToolActionExecution:
        """Execute only the exact owner-approved proposal; never accept replacement args."""
        proposal = self.proposals.get(proposal_id, principal_id=principal_id)
        if proposal is None:
            raise LookupError("approved proposal was not found")
        if proposal.status != "approved":
            raise ValueError("proposal has not been approved")
        if datetime.now(timezone.utc) >= datetime.fromisoformat(proposal.expires_at):
            raise ValueError("approved proposal has expired")
        if self.guarded_runtime.requires_confirmation_by_capability(proposal.tool_name) is not True:
            raise ValueError("tool is no longer classified as a confirmation-required action")
        context = ToolActionContext(
            action_id=proposal.action_id,
            idempotency_key=proposal.idempotency_key,
            principal_id=proposal.principal_id,
            tool_name=proposal.tool_name,
            request_digest=proposal.request_digest,
        )
        context.verify(principal_id, proposal.tool_name, proposal.arguments)
        if not self._claim_proposal_and_reserve_action(
            proposal, principal_id=principal_id,
        ):
            raise ValueError("proposal execution was already claimed or is not approved")
        try:
            approval_id = self.approvals.issue(context, principal_id=principal_id)
        except Exception:
            # Token issuance is a pre-dispatch operation. Its SQLite transaction
            # rolls back on failure, so no provider side effect can have begun.
            # Release the proposal claim instead of incorrectly parking it in
            # outcome_unknown, which requires provider reconciliation.
            released = self._release_pre_dispatch_claim(
                proposal, principal_id=principal_id,
            )
            if not released:
                raise RuntimeError(
                    "approval issuance failed and the pre-dispatch proposal claim "
                    "could not be safely released"
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
