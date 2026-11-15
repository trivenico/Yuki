import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.approved_tool_action_executor import ApprovedToolActionExecutor
from core.guarded_tool_runtime import GuardedToolRuntime
from core.pending_tool_action_proposals import SQLitePendingToolActionProposalStore
from core.tool_action_approvals import SQLiteToolActionApprovalStore
from core.tool_action_context import ToolActionContext
from core.tool_action_journal import ToolActionConflict, ToolActionJournal
from core.tool_capabilities import ToolCapability, ToolCapabilityCatalog, ToolEffect


class AllowPolicy:
    def authorize_tool(self, name):
        return name == "email.send"


class ApprovedToolActionExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        shared_db = str(root / "journal.sqlite")
        self.proposals = SQLitePendingToolActionProposalStore(shared_db)
        self.approvals = SQLiteToolActionApprovalStore(shared_db)
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

    def test_executor_rejects_split_or_mismatched_atomic_store_wiring(self):
        root = Path(self.temp.name)
        split_proposals = SQLitePendingToolActionProposalStore(str(root / "split-proposals.sqlite"))
        with self.assertRaisesRegex(ValueError, "proposal store, approval store"):
            ApprovedToolActionExecutor(split_proposals, self.approvals, self.runtime)
        root = Path(self.temp.name)
        other_approvals = SQLiteToolActionApprovalStore(str(root / "other-approvals.sqlite"))
        with self.assertRaisesRegex(ValueError, "exact approval store"):
            ApprovedToolActionExecutor(self.proposals, other_approvals, self.runtime)

        split_journal = ToolActionJournal(str(root / "split-journal.sqlite"))
        split_runtime = GuardedToolRuntime(
            {"email.send": lambda raw: "sent"}, AllowPolicy(),
            capabilities=ToolCapabilityCatalog({
                "email.send": ToolCapability(
                    ToolEffect.SIDE_EFFECTING, requires_user_confirmation=True,
                ),
            }),
            require_action_context=True,
            action_journal=split_journal,
            approval_store=self.approvals,
        )
        with self.assertRaisesRegex(ValueError, "share one SQLite database"):
            ApprovedToolActionExecutor(self.proposals, self.approvals, split_runtime)

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

    def test_proposal_claim_and_prepared_journal_reservation_are_atomic(self):
        self.assertTrue(self.executor._claim_proposal_and_reserve_action(
            self.proposal, principal_id="owner-a",
        ))
        proposal = self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        )
        action = self.journal.get(self.proposal.idempotency_key)
        self.assertEqual(proposal.execution_state, "executing")
        self.assertEqual(action.state, "prepared")
        self.assertEqual(action.version, 1)
        self.assertEqual(action.action_id, self.proposal.action_id)

        recovered = self.executor.recover_interrupted_execution(
            self.proposal.proposal_id, principal_id="owner-a",
        )
        self.assertEqual(recovered.state, "not_started")
        self.assertEqual(self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        ).execution_state, "not_started")
        self.assertEqual(self.journal.get(self.proposal.idempotency_key).state, "prepared")

        result = self.executor.execute(self.proposal.proposal_id, principal_id="owner-a")
        self.assertEqual(result.state, "finished")
        self.assertEqual(len(self.calls), 1)

    def test_journal_reservation_conflict_rolls_back_proposal_claim(self):
        self.journal.reserve(
            self.proposal.idempotency_key, "email.send",
            {"to": "different@example.com"}, action_id=self.proposal.action_id,
        )
        with self.assertRaises(ToolActionConflict):
            self.executor._claim_proposal_and_reserve_action(
                self.proposal, principal_id="owner-a",
            )
        proposal = self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        )
        self.assertEqual(proposal.execution_state, "not_started")
        self.assertEqual(self.journal.get(self.proposal.idempotency_key).state, "prepared")
        self.assertEqual(self.calls, [])

    def test_atomic_claim_rejects_matching_legacy_reservation(self):
        legacy = self.journal.reserve(
            self.proposal.idempotency_key, "email.send",
            self.arguments, action_id=self.proposal.action_id,
        )
        self.assertEqual(legacy.version, 0)
        with self.assertRaises(ToolActionConflict):
            self.executor._claim_proposal_and_reserve_action(
                self.proposal, principal_id="owner-a",
            )
        proposal = self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        )
        self.assertEqual(proposal.execution_state, "not_started")
        self.assertEqual(self.journal.get(self.proposal.idempotency_key).version, 0)
        self.assertEqual(self.calls, [])

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

    def test_pre_dispatch_release_refuses_when_journal_dispatch_may_have_started(self):
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
        self.assertFalse(self.executor._release_pre_dispatch_claim(
            self.proposal, principal_id="owner-a",
        ))
        self.assertEqual(self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        ).execution_state, "executing")
        self.assertEqual(self.journal.get(self.proposal.idempotency_key).state, "executing")

    def test_approval_token_issue_failure_releases_pre_dispatch_claim(self):
        original_issue = self.approvals.issue
        calls = {"count": 0}

        def fail_once(context, *, principal_id):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("approval storage unavailable")
            return original_issue(context, principal_id=principal_id)

        self.approvals.issue = fail_once
        with self.assertRaisesRegex(RuntimeError, "approval storage unavailable"):
            self.executor.execute(self.proposal.proposal_id, principal_id="owner-a")

        current = self.proposals.get(self.proposal.proposal_id, principal_id="owner-a")
        self.assertEqual(current.execution_state, "not_started")
        reserved = self.journal.get(self.proposal.idempotency_key)
        self.assertEqual(reserved.state, "prepared")
        self.assertEqual(reserved.version, 1)
        self.assertEqual(self.calls, [])

        retried = self.executor.execute(self.proposal.proposal_id, principal_id="owner-a")
        self.assertEqual(retried.state, "finished")
        self.assertEqual(calls["count"], 2)
        self.assertEqual(len(self.calls), 1)

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
        self.assertEqual(
            self.journal.get(self.proposal.idempotency_key).version, 0,
        )
        recovered = self.executor.recover_interrupted_execution(
            self.proposal.proposal_id, principal_id="owner-a",
        )
        self.assertEqual(recovered.state, "executing")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        ).execution_state, "executing")


    def test_keeps_claimed_execution_when_journal_is_only_prepared(self):
        self.assertTrue(self.proposals.claim_execution(
            self.proposal.proposal_id, principal_id="owner-a",
        ))
        self.journal.reserve(
            self.proposal.idempotency_key, "email.send", self.arguments,
            action_id=self.proposal.action_id,
        )
        recovered = self.executor.recover_interrupted_execution(
            self.proposal.proposal_id, principal_id="owner-a",
        )
        self.assertEqual(recovered.state, "executing")
        self.assertEqual(self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        ).execution_state, "executing")
        self.assertEqual(self.calls, [])


    def test_expired_pre_dispatch_claim_releases_proposal_for_safe_retry(self):
        self.assertTrue(self.proposals.claim_execution(
            self.proposal.proposal_id, principal_id="owner-a",
        ))
        reserved = self.journal.reserve(
            self.proposal.idempotency_key, "email.send", self.arguments,
            action_id=self.proposal.action_id,
        )
        claimed = self.journal.claim_execution(
            self.proposal.idempotency_key, reserved.request_digest,
        )
        self.assertEqual(claimed.state, "authorizing")
        db = sqlite3.connect(self.journal.database_path)
        try:
            db.execute(
                "UPDATE tool_actions SET lease_expires_at=? WHERE idempotency_key=?",
                ("2000-01-01T00:00:00+00:00", self.proposal.idempotency_key),
            )
            db.commit()
        finally:
            db.close()

        recovered = self.executor.recover_interrupted_execution(
            self.proposal.proposal_id, principal_id="owner-a",
        )
        self.assertEqual(recovered.state, "not_started")
        self.assertEqual(self.journal.get(self.proposal.idempotency_key).state, "prepared")
        self.assertEqual(self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        ).execution_state, "not_started")

        retried = self.executor.execute(self.proposal.proposal_id, principal_id="owner-a")
        self.assertEqual(retried.state, "finished")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.journal.get(self.proposal.idempotency_key).state, "succeeded")

    def test_expired_journal_lease_recovers_proposal_as_unknown(self):
        self.assertTrue(self.proposals.claim_execution(
            self.proposal.proposal_id, principal_id="owner-a",
        ))
        reserved = self.journal.reserve(
            self.proposal.idempotency_key, "email.send", self.arguments,
            action_id=self.proposal.action_id,
        )
        self.journal.begin_execution(
            self.proposal.idempotency_key, reserved.request_digest,
        )
        db = sqlite3.connect(self.journal.database_path)
        try:
            db.execute(
                "UPDATE tool_actions SET lease_expires_at=? WHERE idempotency_key=?",
                ("2000-01-01T00:00:00+00:00", self.proposal.idempotency_key),
            )
            db.commit()
        finally:
            db.close()
        recovered = self.executor.recover_interrupted_execution(
            self.proposal.proposal_id, principal_id="owner-a",
        )
        self.assertEqual(recovered.state, "unknown")
        self.assertEqual(self.journal.get(self.proposal.idempotency_key).state, "outcome_unknown")
        self.assertEqual(self.proposals.get(
            self.proposal.proposal_id, principal_id="owner-a",
        ).execution_state, "unknown")
        self.assertEqual(self.calls, [])

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
        self.assertEqual(self.journal.get(self.proposal.idempotency_key).state, "prepared")
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
