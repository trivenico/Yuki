"""Tests for durable pending tool-action proposal lifecycle."""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.pending_tool_action_proposals import SQLitePendingToolActionProposalStore
from core.tool_action_context import ToolActionContext


class SQLitePendingToolActionProposalStoreTests(unittest.TestCase):
    def make_store(self, directory, **kwargs):
        return SQLitePendingToolActionProposalStore(
            str(Path(directory) / "pending-proposals.sqlite3"), **kwargs,
        )

    def make_proposal(self, store, principal="user-1"):
        arguments = {"to": "person@example.com", "subject": "Quarterly update"}
        context = ToolActionContext.create(principal, "email.send", arguments)
        return store.create(context, arguments, principal_id=principal)

    def test_proposal_persists_exact_action_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            proposal = self.make_proposal(self.make_store(directory))
            reopened = self.make_store(directory)
            loaded = reopened.get(proposal.proposal_id, principal_id="user-1")
            self.assertEqual(loaded, proposal)
            self.assertEqual(loaded.arguments, {
                "to": "person@example.com", "subject": "Quarterly update",
            })
            self.assertEqual(reopened.list_pending(principal_id="user-1"), (loaded,))

    def test_proposals_are_scoped_to_authenticated_principal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            proposal = self.make_proposal(store)
            self.assertIsNone(store.get(proposal.proposal_id, principal_id="user-2"))
            self.assertEqual(store.list_pending(principal_id="user-2"), ())
            self.assertIsNone(store.decide(
                proposal.proposal_id, principal_id="user-2", decision="approved",
            ))
            self.assertEqual(store.get(
                proposal.proposal_id, principal_id="user-1",
            ).status, "pending")

    def test_action_context_must_match_exact_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            context = ToolActionContext.create("user-1", "email.send", {"to": "a@example.com"})
            with self.assertRaises(ValueError):
                store.create(context, {"to": "b@example.com"}, principal_id="user-1")
            with self.assertRaises(ValueError):
                store.create(context, {"to": "a@example.com"}, principal_id="user-2")

    def test_approve_and_reject_are_terminal_single_use_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            approved = self.make_proposal(store)
            result = store.decide(
                approved.proposal_id, principal_id="user-1", decision="approved",
            )
            self.assertEqual(result.status, "approved")
            self.assertIsNone(store.decide(
                approved.proposal_id, principal_id="user-1", decision="rejected",
            ))
            self.assertEqual(store.get(
                approved.proposal_id, principal_id="user-1",
            ).status, "approved")
            rejected = self.make_proposal(store)
            result = store.decide(
                rejected.proposal_id, principal_id="user-1", decision="rejected",
            )
            self.assertEqual(result.status, "rejected")
            self.assertIsNone(store.decide(
                rejected.proposal_id, principal_id="user-1", decision="approved",
            ))

    def test_concurrent_approval_has_exactly_one_winner(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            proposal = self.make_proposal(store)
            barrier = threading.Barrier(3)
            results = []
            lock = threading.Lock()

            def approve():
                barrier.wait()
                result = store.decide(
                    proposal.proposal_id, principal_id="user-1", decision="approved",
                )
                with lock:
                    results.append(result)

            workers = [threading.Thread(target=approve) for _ in range(2)]
            for worker in workers:
                worker.start()
            barrier.wait()
            for worker in workers:
                worker.join(timeout=5)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(sum(result is not None for result in results), 1)

    def test_expired_proposal_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory, ttl_seconds=1)
            proposal = self.make_proposal(store)
            time.sleep(1.05)
            self.assertIsNone(store.decide(
                proposal.proposal_id, principal_id="user-1", decision="approved",
            ))
            self.assertEqual(store.get(
                proposal.proposal_id, principal_id="user-1",
            ).status, "expired")

    def test_cancel_only_affects_pending_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            proposal = self.make_proposal(store)
            self.assertTrue(store.cancel(proposal.proposal_id, principal_id="user-1"))
            self.assertFalse(store.cancel(proposal.proposal_id, principal_id="user-1"))
            self.assertEqual(store.get(
                proposal.proposal_id, principal_id="user-1",
            ).status, "cancelled")

    def test_invalid_ttl_and_limit_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for ttl in (0, -1, True, 3601):
                with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                    self.make_store(directory, ttl_seconds=ttl)
            store = self.make_store(directory)
            with self.assertRaises(ValueError):
                store.list_pending(principal_id="user-1", limit=0)


if __name__ == "__main__":
    unittest.main()
