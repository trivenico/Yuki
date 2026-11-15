import tempfile
import unittest
from pathlib import Path

from core.approved_tool_action_executor import ApprovedToolActionExecutor
from core.guarded_tool_runtime import GuardedToolRuntime
from core.pending_tool_action_proposals import SQLitePendingToolActionProposalStore
from core.tool_action_approvals import SQLiteToolActionApprovalStore
from core.tool_action_context import ToolActionContext
from core.tool_action_journal import ToolActionJournal
from core.tool_capabilities import ToolCapability, ToolCapabilityCatalog, ToolEffect


class AllowPolicy:
    def authorize_tool(self, name):
        return name == "email.send"


class ApprovedToolActionExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.proposals = SQLitePendingToolActionProposalStore(str(root / "proposals.sqlite"))
        self.approvals = SQLiteToolActionApprovalStore(str(root / "approvals.sqlite"))
        self.journal = ToolActionJournal(str(root / "journal.sqlite"))
        self.calls = []
        self.runtime = GuardedToolRuntime(
            {"email.send": lambda raw: self.calls.append(raw) or "sent"},
            AllowPolicy(),
            capabilities=ToolCapabilityCatalog({
                "email.send": ToolCapability(
                    ToolEffect.SIDE_EFFECTING, requires_user_confirmation=True,
                ),
            }),
            require_action_context=True,
            action_journal=self.journal,
            approval_store=self.approvals,
        )
        self.executor = ApprovedToolActionExecutor(
            self.proposals, self.approvals, self.runtime,
        )
        self.arguments = {"to": "person@example.com", "subject": "Hello"}
        context = ToolActionContext.create("owner-a", "email.send", self.arguments)
        proposal = self.proposals.create(context, self.arguments, principal_id="owner-a")
        self.proposal = self.proposals.decide(
            proposal.proposal_id, principal_id="owner-a", decision="approved",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_executes_exact_persisted_arguments_and_journals_success(self):
        result = self.executor.execute(self.proposal.proposal_id, principal_id="owner-a")
        self.assertEqual(result.state, "finished")
        self.assertEqual(result.result, "sent")
        self.assertEqual(len(self.calls), 1)
        action = self.journal.get(self.proposal.idempotency_key)
        self.assertEqual(action.state, "succeeded")
        self.assertEqual(action.action_id, self.proposal.action_id)
        self.assertEqual(self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        ).execution_state, "finished")

    def test_execution_is_single_claim_and_cannot_be_replayed(self):
        self.executor.execute(self.proposal.proposal_id, principal_id="owner-a")
        with self.assertRaisesRegex(ValueError, "already claimed"):
            self.executor.execute(self.proposal.proposal_id, principal_id="owner-a")
        self.assertEqual(len(self.calls), 1)

    def test_other_principal_cannot_execute_proposal(self):
        with self.assertRaises(LookupError):
            self.executor.execute(self.proposal.proposal_id, principal_id="owner-b")
        self.assertEqual(self.calls, [])

    def test_unapproved_proposal_cannot_execute(self):
        args = {"to": "other@example.com"}
        context = ToolActionContext.create("owner-a", "email.send", args)
        pending = self.proposals.create(context, args, principal_id="owner-a")
        with self.assertRaisesRegex(ValueError, "not been approved"):
            self.executor.execute(pending.proposal_id, principal_id="owner-a")
        self.assertEqual(self.calls, [])

    def test_handler_exception_is_recorded_as_unknown_and_never_replayed(self):
        broken = GuardedToolRuntime(
            {"email.send": lambda raw: (_ for _ in ()).throw(RuntimeError("provider timeout"))},
            AllowPolicy(),
            capabilities=ToolCapabilityCatalog({
                "email.send": ToolCapability(
                    ToolEffect.SIDE_EFFECTING, requires_user_confirmation=True,
                ),
            }),
            require_action_context=True,
            action_journal=self.journal,
            approval_store=self.approvals,
        )
        executor = ApprovedToolActionExecutor(self.proposals, self.approvals, broken)
        result = executor.execute(self.proposal.proposal_id, principal_id="owner-a")
        self.assertIn("reconciliation", result.result)
        action = self.journal.get(self.proposal.idempotency_key)
        self.assertEqual(action.state, "outcome_unknown")
        self.assertEqual(self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        ).execution_state, "unknown")





    def test_recovers_claim_from_durable_success_without_redispatch(self):
        self.assertTrue(self.proposals.claim_execution(
            self.proposal.proposal_id, principal_id="owner-a",
        ))
        reserved = self.journal.reserve(
            self.proposal.idempotency_key, "email.send", self.arguments,
            action_id=self.proposal.action_id,
        )
        running = self.journal.begin_execution(
            self.proposal.idempotency_key, reserved.request_digest,
        )
        self.journal.mark_succeeded(
            self.proposal.idempotency_key, running.lease_owner, "provider-confirmed",
        )
        recovered = self.executor.recover_interrupted_execution(
            self.proposal.proposal_id, principal_id="owner-a",
        )
        self.assertEqual(recovered.state, "finished")
        self.assertEqual(recovered.result, "provider-confirmed")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        ).execution_state, "finished")

    def test_keeps_claimed_execution_when_journal_reservation_is_not_yet_visible(self):
        self.assertTrue(self.proposals.claim_execution(
            self.proposal.proposal_id, principal_id="owner-a",
        ))
        recovered = self.executor.recover_interrupted_execution(
            self.proposal.proposal_id, principal_id="owner-a",
        )
        self.assertEqual(recovered.state, "executing")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        ).execution_state, "executing")

    def test_blocked_dispatch_is_not_reported_as_success(self):
        class DenyPolicy:
            def authorize_tool(self, name):
                return False

        blocked = GuardedToolRuntime(
            {"email.send": lambda raw: self.calls.append(raw) or "sent"},
            DenyPolicy(),
            capabilities=ToolCapabilityCatalog({
                "email.send": ToolCapability(
                    ToolEffect.SIDE_EFFECTING, requires_user_confirmation=True,
                ),
            }),
            require_action_context=True,
            action_journal=self.journal,
            approval_store=self.approvals,
        )
        executor = ApprovedToolActionExecutor(self.proposals, self.approvals, blocked)
        result = executor.execute(self.proposal.proposal_id, principal_id="owner-a")
        self.assertEqual(result.state, "unknown")
        self.assertIn("blocked", result.result.lower())
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.journal.get(self.proposal.idempotency_key))
        self.assertEqual(self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        ).execution_state, "unknown")

    def test_rejects_proposal_if_host_capability_no_longer_requires_confirmation(self):
        changed = GuardedToolRuntime(
            {"email.send": lambda raw: self.calls.append(raw) or "sent"},
            AllowPolicy(),
            capabilities=ToolCapabilityCatalog({
                "email.send": ToolCapability(ToolEffect.READ_ONLY),
            }),
            require_action_context=True,
            action_journal=self.journal,
            approval_store=self.approvals,
        )
        executor = ApprovedToolActionExecutor(self.proposals, self.approvals, changed)
        with self.assertRaisesRegex(ValueError, "no longer classified"):
            executor.execute(self.proposal.proposal_id, principal_id="owner-a")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        ).execution_state, "not_started")
