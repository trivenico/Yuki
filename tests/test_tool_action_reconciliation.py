import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.tool_action_journal import ToolActionJournal
from core.pending_tool_action_proposals import SQLitePendingToolActionProposalStore
from core.tool_action_context import ToolActionContext
from core.tool_action_reconciliation import (
    ProviderReconciliationResult,
    ToolActionReconciliationRegistry,
    ToolActionReconciliationService,
)


class FakeProvider:
    def __init__(self, observation):
        self.observation = observation
        self.calls = 0

    def reconcile(self, *, action, proposal):
        self.calls += 1
        self.asserted_action = action
        self.asserted_proposal = proposal
        return self.observation


class ToolActionReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = ToolActionJournal(str(Path(self.temp.name) / "journal.sqlite"))
        self.record = self.journal.reserve(
            "provider-action-1", "email.send", {"to": "person@example.com"},
            action_id="a" * 32,
        )
        started = self.journal.begin_execution(
            self.record.idempotency_key, self.record.request_digest,
        )
        self.journal.mark_outcome_unknown(
            self.record.idempotency_key, started.lease_owner, "provider_timeout",
        )
        self.action = self.journal.get(self.record.idempotency_key)
        self.proposal = SimpleNamespace(
            action_id=self.action.action_id,
            idempotency_key=self.action.idempotency_key,
            tool_name=self.action.tool_name,
            request_digest=self.action.request_digest,
            arguments={"to": "person@example.com"},
        )

    def service(self, observation):
        adapter = FakeProvider(observation)
        registry = ToolActionReconciliationRegistry({"email.send": adapter})
        return ToolActionReconciliationService(self.journal, registry), adapter

    def test_provider_confirmed_success_is_durably_recorded_with_evidence(self):
        service, adapter = self.service(ProviderReconciliationResult(
            "succeeded", "gmail-message:abc123", {"provider_status": "SENT"},
        ))
        result = service.reconcile(action=self.action, proposal=self.proposal)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(result.result["evidence_reference"], "gmail-message:abc123")
        self.assertEqual(adapter.calls, 1)

    def test_provider_confirmed_non_execution_is_durably_recorded_with_evidence(self):
        service, _ = self.service(ProviderReconciliationResult(
            "not_executed", "gmail-query:checked-at-2026-11-15",
        ))
        result = service.reconcile(action=self.action, proposal=self.proposal)
        self.assertEqual(result.state, "not_executed")
        self.assertEqual(result.error_code, "provider_confirmed_not_executed")
        self.assertEqual(result.result["evidence_reference"], "gmail-query:checked-at-2026-11-15")

    def test_inconclusive_provider_lookup_preserves_unknown_state(self):
        service, _ = self.service(ProviderReconciliationResult("unknown", "provider-query:pending"))
        result = service.reconcile(action=self.action, proposal=self.proposal)
        self.assertEqual(result.state, "outcome_unknown")
        self.assertEqual(self.journal.get(self.action.idempotency_key).state, "outcome_unknown")

    def test_rejects_proposal_identity_mismatch_before_provider_call(self):
        service, adapter = self.service(ProviderReconciliationResult("unknown", "query:1"))
        wrong = SimpleNamespace(**{**vars(self.proposal), "action_id": "b" * 32})
        with self.assertRaisesRegex(ValueError, "identity"):
            service.reconcile(action=self.action, proposal=wrong)
        self.assertEqual(adapter.calls, 0)

    def test_requires_unknown_journal_state_and_registered_adapter(self):
        succeeded = self.journal.reconcile_succeeded(self.action.idempotency_key, {"ok": True})
        service, _ = self.service(ProviderReconciliationResult("unknown", "query:2"))
        with self.assertRaisesRegex(ValueError, "outcome_unknown"):
            service.reconcile(action=succeeded, proposal=self.proposal)
        missing = ToolActionReconciliationService(
            self.journal, ToolActionReconciliationRegistry({}),
        )
        with self.assertRaises(LookupError):
            missing.reconcile(action=self.action, proposal=self.proposal)

    def test_owner_scoped_proposal_reconciliation_marks_confirmed_success_finished(self):
        proposals = SQLitePendingToolActionProposalStore(
            str(Path(self.temp.name) / "proposals.sqlite"),
        )
        context = ToolActionContext(
            action_id=self.action.action_id,
            idempotency_key=self.action.idempotency_key,
            principal_id="owner-a",
            tool_name=self.action.tool_name,
            request_digest=self.action.request_digest,
        )
        proposal = proposals.create(
            context, self.proposal.arguments, principal_id="owner-a",
        )
        proposals.decide(proposal.proposal_id, principal_id="owner-a", decision="approved")
        proposals.claim_execution(proposal.proposal_id, principal_id="owner-a")
        proposals.finish_execution(
            proposal.proposal_id, principal_id="owner-a", outcome_unknown=True,
        )
        adapter = FakeProvider(ProviderReconciliationResult(
            "succeeded", "provider-message:confirmed", {"status": "sent"},
        ))
        service = ToolActionReconciliationService(
            self.journal, ToolActionReconciliationRegistry({"email.send": adapter}),
            proposals=proposals,
        )
        with self.assertRaises(LookupError):
            service.reconcile_proposal(proposal.proposal_id, principal_id="owner-b")
        result = service.reconcile_proposal(proposal.proposal_id, principal_id="owner-a")
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(proposals.get(
            proposal.proposal_id, principal_id="owner-a",
        ).execution_state, "finished")
        self.assertEqual(adapter.calls, 1)

    def test_provider_confirmed_non_execution_does_not_make_proposal_replayable(self):
        proposals = SQLitePendingToolActionProposalStore(
            str(Path(self.temp.name) / "proposals.sqlite"),
        )
        context = ToolActionContext(
            action_id=self.action.action_id,
            idempotency_key=self.action.idempotency_key,
            principal_id="owner-a",
            tool_name=self.action.tool_name,
            request_digest=self.action.request_digest,
        )
        proposal = proposals.create(context, self.proposal.arguments, principal_id="owner-a")
        proposals.decide(proposal.proposal_id, principal_id="owner-a", decision="approved")
        proposals.claim_execution(proposal.proposal_id, principal_id="owner-a")
        proposals.finish_execution(proposal.proposal_id, principal_id="owner-a", outcome_unknown=True)
        adapter = FakeProvider(ProviderReconciliationResult(
            "not_executed", "provider-query:confirmed-absent",
        ))
        service = ToolActionReconciliationService(
            self.journal, ToolActionReconciliationRegistry({"email.send": adapter}),
            proposals=proposals,
        )
        service.reconcile_proposal(proposal.proposal_id, principal_id="owner-a")
        current = proposals.get(proposal.proposal_id, principal_id="owner-a")
        self.assertEqual(current.execution_state, "unknown")
        with self.assertRaisesRegex(ValueError, "not awaiting reconciliation"):
            service.reconcile_proposal(proposal.proposal_id, principal_id="owner-a")
        self.assertEqual(adapter.calls, 1)

    def test_validates_provider_evidence_contract(self):
        for outcome, evidence in (("success", "ref"), ("unknown", ""), ("unknown", "x" * 513)):
            with self.subTest(outcome=outcome, evidence_length=len(evidence)), self.assertRaises(ValueError):
                ProviderReconciliationResult(outcome, evidence)


if __name__ == "__main__":
    unittest.main()
