import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.observation_event_buffer import ObservationEventBuffer
from core.observation_knowledge_store import SQLiteObservationKnowledgeReview
from core.observation_learning import ObservationLearningWorker
from core.observation_sources import ObservationEvent


class SQLiteObservationKnowledgeReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "knowledge-review.sqlite3")
        self.clock = [1000.0]

    def store(self, owner="user-a", **kwargs):
        return SQLiteObservationKnowledgeReview(
            self.path, owner_id=owner, clock=lambda: self.clock[0], **kwargs
        )

    def test_proposals_survive_reconstruction_and_review_state_is_durable(self):
        first = self.store()
        proposal = first.propose("application", "com.example.editor")
        reopened = self.store()
        loaded = reopened.get(proposal.proposal_id)
        self.assertEqual(loaded, proposal)
        approved = reopened.approve(proposal.proposal_id, reviewer_id="operator-1")
        self.assertEqual(approved.status, "approved")
        self.assertEqual(self.store().get(proposal.proposal_id).status, "approved")

    def test_owner_scope_prevents_cross_user_reads_and_mutations(self):
        owner_a = self.store("user-a")
        owner_b = self.store("user-b")
        proposal = owner_a.propose("browser_domain", "docs.example.com")
        self.assertIsNone(owner_b.get(proposal.proposal_id))
        self.assertFalse(owner_b.delete(proposal.proposal_id))
        self.assertEqual(owner_b.list_pending(), ())
        self.assertEqual(owner_a.get(proposal.proposal_id), proposal)

    def test_worker_accepts_durable_review_store(self):
        buffer = ObservationEventBuffer(capacity=4)
        store = self.store()
        worker = ObservationLearningWorker(buffer, store)
        buffer(ObservationEvent(
            "desktop:metadata", "browser_domain", {"domain": "docs.example.com"}
        ))
        proposal, = worker.process_batch()
        self.assertEqual(store.list_pending(), (proposal,))

    def test_capacity_is_bounded_and_duplicate_is_idempotent(self):
        store = self.store(capacity=1)
        first = store.propose("application", "com.example.editor")
        duplicate = store.propose("application", "com.example.editor")
        self.assertEqual(duplicate.proposal_id, first.proposal_id)
        with self.assertRaisesRegex(BufferError, "capacity"):
            store.propose("application", "com.example.terminal")

    def test_expired_records_are_purged_by_retention_policy(self):
        store = self.store(retention_seconds=60)
        proposal = store.propose("application", "com.example.editor")
        self.clock[0] += 61
        # Direct lookup must enforce expiry too, not only list operations.
        self.assertIsNone(store.get(proposal.proposal_id))
        self.assertEqual(store.list_pending(), ())

    def test_rejects_invalid_metadata_and_review_transitions(self):
        store = self.store()
        with self.assertRaises(ValueError):
            store.propose("application", "C:\\Users\\private")
        proposal = store.propose("application", "com.example.editor")
        self.assertEqual(store.reject(proposal.proposal_id, reviewer_id="operator-1").status, "rejected")
        with self.assertRaises(RuntimeError):
            store.approve(proposal.proposal_id, reviewer_id="operator-1")
        with self.assertRaises(KeyError):
            store.approve("missing", reviewer_id="operator-1")

    def test_delete_and_clear_are_owner_scoped(self):
        store_a = self.store("user-a")
        store_b = self.store("user-b")
        first = store_a.propose("application", "com.example.editor")
        other = store_b.propose("application", "com.example.editor")
        self.assertTrue(store_a.delete(first.proposal_id))
        self.assertEqual(store_a.clear(), 0)
        self.assertEqual(store_b.get(other.proposal_id), other)

    @unittest.skipUnless(os.name == "posix", "POSIX permission contract")
    def test_database_file_is_private(self):
        self.store()
        self.assertEqual(Path(self.path).stat().st_mode & 0o777, 0o600)

    def test_expired_proposal_cannot_be_approved_or_rejected(self):
        store = self.store(retention_seconds=60)
        proposal = store.propose("application", "com.example.editor")
        self.clock[0] += 61
        with self.assertRaises(KeyError):
            store.approve(proposal.proposal_id, reviewer_id="operator-1")
        with self.assertRaises(KeyError):
            store.reject(proposal.proposal_id, reviewer_id="operator-1")
        self.assertIsNone(store.get(proposal.proposal_id))

    def test_review_audit_records_reviewer_and_decision(self):
        store = self.store()
        proposal = store.propose("application", "com.example.editor")
        approved = store.approve(proposal.proposal_id, reviewer_id="  operator-42  ")
        self.assertEqual(approved.status, "approved")
        events = store.list_review_events(proposal.proposal_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["decision"], "approved")
        self.assertEqual(events[0]["reviewer_id"], "operator-42")
        self.assertEqual(events[0]["created_at"], self.clock[0])
        with self.assertRaises(ValueError):
            store.reject(proposal.proposal_id, reviewer_id="")
        with self.assertRaises(TypeError):
            store.approve(proposal.proposal_id)

    def test_review_audit_is_owner_scoped_and_deleted_with_proposal(self):
        owner_a = self.store("user-a")
        owner_b = self.store("user-b")
        proposal = owner_a.propose("application", "com.example.editor")
        owner_a.reject(proposal.proposal_id, reviewer_id="operator-a")
        self.assertEqual(owner_b.list_review_events(proposal.proposal_id), ())
        self.assertEqual(owner_a.list_review_events(proposal.proposal_id)[0]["reviewer_id"], "operator-a")
        self.assertTrue(owner_a.delete(proposal.proposal_id))
        self.assertEqual(owner_a.list_review_events(proposal.proposal_id), ())

    def test_expired_review_attempt_commits_retention_cleanup(self):
        store = self.store(retention_seconds=60)
        proposal = store.propose("application", "com.example.editor")
        self.clock[0] += 61
        with self.assertRaises(KeyError):
            store.approve(proposal.proposal_id, reviewer_id="operator-1")
        with sqlite3.connect(self.path) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM observation_knowledge_proposals WHERE proposal_id=?",
                (proposal.proposal_id,),
            ).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
