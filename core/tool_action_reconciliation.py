"""Provider-neutral reconciliation contracts for uncertain side effects.

Adapters are trusted host integrations. They must query the authoritative
provider using durable action identity and return evidence-backed outcomes;
model-authored claims are never accepted as reconciliation evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from core.tool_action_journal import ToolAction, ToolActionJournal
from core.pending_tool_action_proposals import SQLitePendingToolActionProposalStore


@dataclass(frozen=True)
class ProviderReconciliationResult:
    """Authoritative provider observation for one previously uncertain action."""

    outcome: str
    evidence_reference: str
    provider_result: Any = None

    def __post_init__(self) -> None:
        if self.outcome not in {"succeeded", "not_executed", "unknown"}:
            raise ValueError("outcome must be succeeded, not_executed, or unknown")
        if (
            not isinstance(self.evidence_reference, str)
            or not self.evidence_reference.strip()
            or len(self.evidence_reference) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in self.evidence_reference)
        ):
            raise ValueError("evidence_reference must be a bounded, non-empty printable string")


class ProviderActionReconciliationAdapter(Protocol):
    """Trusted adapter that checks a provider without issuing the action again."""

    def reconcile(
        self, *, action: ToolAction, proposal: Any,
    ) -> ProviderReconciliationResult:
        """Return evidence from a provider lookup; never retry the side effect."""
        ...


class ToolActionReconciliationRegistry:
    """Immutable-by-copy map of tool names to trusted provider lookup adapters."""

    def __init__(self, adapters: Mapping[str, ProviderActionReconciliationAdapter]):
        if not isinstance(adapters, Mapping):
            raise TypeError("adapters must be a mapping")
        copied = dict(adapters)
        for name, adapter in copied.items():
            if (
                not isinstance(name, str) or not name
                or not callable(getattr(adapter, "reconcile", None))
            ):
                raise TypeError("each tool requires a trusted reconcile adapter")
        self._adapters = copied

    def resolve(self, tool_name: str) -> ProviderActionReconciliationAdapter | None:
        return self._adapters.get(tool_name)


class ToolActionReconciliationService:
    """Apply provider-confirmed outcomes to the durable action journal."""

    def __init__(
        self, journal: ToolActionJournal,
        registry: ToolActionReconciliationRegistry,
        proposals: SQLitePendingToolActionProposalStore | None = None,
    ):
        if not isinstance(journal, ToolActionJournal):
            raise TypeError("journal must be a ToolActionJournal")
        if not isinstance(registry, ToolActionReconciliationRegistry):
            raise TypeError("registry must be a ToolActionReconciliationRegistry")
        if proposals is not None and not isinstance(proposals, SQLitePendingToolActionProposalStore):
            raise TypeError("proposals must be a SQLitePendingToolActionProposalStore")
        self.journal = journal
        self.registry = registry
        self.proposals = proposals

    def reconcile(self, *, action: ToolAction, proposal: Any) -> ToolAction:
        if not isinstance(action, ToolAction) or action.state != "outcome_unknown":
            raise ValueError("only outcome_unknown journal actions can be reconciled")
        if (
            getattr(proposal, "action_id", None) != action.action_id
            or getattr(proposal, "idempotency_key", None) != action.idempotency_key
            or getattr(proposal, "tool_name", None) != action.tool_name
            or getattr(proposal, "request_digest", None) != action.request_digest
        ):
            raise ValueError("proposal identity does not match the journal action")
        adapter = self.registry.resolve(action.tool_name)
        if adapter is None:
            raise LookupError("no provider reconciliation adapter is registered")
        observation = adapter.reconcile(action=action, proposal=proposal)
        if not isinstance(observation, ProviderReconciliationResult):
            raise TypeError("provider adapter must return ProviderReconciliationResult")
        evidence = {
            "evidence_reference": observation.evidence_reference,
            "provider_result": observation.provider_result,
        }
        if observation.outcome == "succeeded":
            return self.journal.reconcile_succeeded(action.idempotency_key, evidence)
        if observation.outcome == "not_executed":
            return self.journal.reconcile_not_executed(
                action.idempotency_key, "provider_confirmed_not_executed",
                evidence=evidence,
            )
        # An inconclusive lookup must not mutate the durable action state.
        return action

    def reconcile_proposal(self, proposal_id: str, *, principal_id: str) -> ToolAction:
        """Reconcile one owner-scoped proposal; never dispatch or retry its tool."""
        if self.proposals is None:
            raise RuntimeError("proposal reconciliation is not configured")
        proposal = self.proposals.get(proposal_id, principal_id=principal_id)
        if proposal is None:
            raise LookupError("proposal was not found")
        if proposal.status != "approved" or proposal.execution_state != "unknown":
            raise ValueError("proposal is not awaiting reconciliation")
        action = self.journal.get(proposal.idempotency_key)
        if action is None or action.state != "outcome_unknown":
            raise ValueError("journal action is not awaiting reconciliation")
        reconciled = self.reconcile(action=action, proposal=proposal)
        if reconciled.state == "succeeded":
            finished = self.proposals.finish_reconciliation(
                proposal_id, principal_id=principal_id,
            )
            if not finished:
                current = self.proposals.get(proposal_id, principal_id=principal_id)
                if current is None or current.execution_state != "finished":
                    raise RuntimeError(
                        "provider outcome is confirmed but proposal finalization needs recovery"
                    )
        return reconciled
