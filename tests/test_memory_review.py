import tempfile
import unittest
from pathlib import Path

from core.memory import MemoryStore
from core.memory_review import MemoryReviewStore


class MemoryReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.memories = MemoryStore(root / "memories.sqlite3")
        self.review = MemoryReviewStore(root / "review.sqlite3", self.memories)

    def propose(self, **kwargs):
        return self.review.propose(actor_id="alice", scope_id="personal:alice",
            category="preference", content="Prefer concise answers", **kwargs)

    def test_proposal_is_not_memory_until_approved(self):
        item = self.propose()
        self.assertEqual(item.status, "pending")
        self.assertEqual(self.memories.list(actor_id="alice",
            scope_id="personal:alice"), [])
        approved = self.review.approve(actor_id="alice",
            scope_id="personal:alice", proposal_id=item.proposal_id)
        self.assertEqual(approved.content, "Prefer concise answers")
        self.assertEqual(self.review.list(actor_id="alice",
            scope_id="personal:alice", status="approved")[0].status, "approved")

    def test_approval_is_idempotent(self):
        item = self.propose()
        first = self.review.approve(actor_id="alice",
            scope_id="personal:alice", proposal_id=item.proposal_id)
        second = self.review.approve(actor_id="alice",
            scope_id="personal:alice", proposal_id=item.proposal_id)
        self.assertEqual(first.memory_id, second.memory_id)
        self.assertEqual(len(self.memories.list(actor_id="alice",
            scope_id="personal:alice")), 1)

    def test_owner_and_scope_isolation(self):
        item = self.propose()
        self.assertEqual(self.review.list(actor_id="mallory",
            scope_id="personal:alice"), [])
        with self.assertRaises(KeyError):
            self.review.approve(actor_id="mallory",
                scope_id="personal:alice", proposal_id=item.proposal_id)
        with self.assertRaises(KeyError):
            self.review.approve(actor_id="alice",
                scope_id="personal:bob", proposal_id=item.proposal_id)

    def test_reject_prevents_approval(self):
        item = self.propose()
        self.assertTrue(self.review.reject(actor_id="alice",
            scope_id="personal:alice", proposal_id=item.proposal_id))
        with self.assertRaises(ValueError):
            self.review.approve(actor_id="alice",
                scope_id="personal:alice", proposal_id=item.proposal_id)
        self.assertEqual(self.memories.list(actor_id="alice",
            scope_id="personal:alice"), [])

    def test_approved_proposal_cannot_be_rejected(self):
        item = self.propose()
        self.review.approve(actor_id="alice", scope_id="personal:alice",
            proposal_id=item.proposal_id)
        with self.assertRaises(ValueError):
            self.review.reject(actor_id="alice", scope_id="personal:alice",
                proposal_id=item.proposal_id)

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.review.propose(actor_id="alice", scope_id="personal:alice",
                category="unknown", content="bad")
        with self.assertRaises(ValueError):
            self.review.list(actor_id="alice", scope_id="personal:alice",
                status="invalid")


if __name__ == "__main__":
    unittest.main()
