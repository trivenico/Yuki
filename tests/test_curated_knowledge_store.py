import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.curated_knowledge_store import SQLiteCuratedKnowledge
from core.observation_knowledge_store import SQLiteObservationKnowledgeReview


class SQLiteCuratedKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.review = SQLiteObservationKnowledgeReview(
            str(Path(self.tmp.name) / "review.sqlite3"), owner_id="owner-1"
        )
        self.catalog = SQLiteCuratedKnowledge(
            str(Path(self.tmp.name) / "curated.sqlite3"), owner_id="owner-1"
        )

    def _approved(self, value="com.example.editor"):
        proposal = self.review.propose("application", value)
        return self.review.approve(proposal.proposal_id, reviewer_id="reviewer-1")

    def test_promote_persists_and_preserves_provenance(self):
        proposal = self._approved()
        entry_id = self.catalog.promote(
            proposal, approved_by="reviewer-1", promoted_by="admin-1"
        )
        entry, = self.catalog.list_active()
        self.assertEqual(entry["entry_id"], entry_id)
        self.assertEqual(entry["source_proposal_id"], proposal.proposal_id)
        self.assertEqual(entry["approved_by"], "reviewer-1")
        self.assertEqual(entry["promoted_by"], "admin-1")
        reopened = SQLiteCuratedKnowledge(
            str(Path(self.tmp.name) / "curated.sqlite3"), owner_id="owner-1"
        )
        self.assertEqual(reopened.list_active(), (entry,))

    def test_promotion_is_idempotent_for_source_and_semantic_duplicate(self):
        proposal = self._approved()
        first = self.catalog.promote(
            proposal, approved_by="reviewer-1", promoted_by="admin-1"
        )
        self.assertEqual(
            self.catalog.promote(
                proposal, approved_by="reviewer-1", promoted_by="admin-1"
            ),
            first,
        )
        duplicate = SimpleNamespace(
            proposal_id="another-approved-proposal",
            status="approved",
            kind=proposal.kind,
            value=proposal.value,
            summary=proposal.summary,
        )
        self.assertEqual(
            self.catalog.promote(
                duplicate, approved_by="reviewer-2", promoted_by="admin-1"
            ),
            first,
        )
        self.assertEqual(len(self.catalog.list_active()), 1)

    def test_pending_or_unsupported_proposal_is_rejected(self):
        pending = self.review.propose("application", "com.example.pending")
        with self.assertRaises(PermissionError):
            self.catalog.promote(
                pending, approved_by="reviewer-1", promoted_by="admin-1"
            )
        unsupported = SimpleNamespace(
            proposal_id="bad", status="approved", kind="free_text",
            value="arbitrary instructions", summary="unsafe",
        )
        with self.assertRaises(ValueError):
            self.catalog.promote(
                unsupported, approved_by="reviewer-1", promoted_by="admin-1"
            )

    def test_capacity_and_deactivation_are_bounded_and_audited(self):
        catalog = SQLiteCuratedKnowledge(
            str(Path(self.tmp.name) / "small.sqlite3"),
            owner_id="owner-1", capacity=1,
        )
        first = self._approved("com.example.one")
        entry_id = catalog.promote(
            first, approved_by="reviewer-1", promoted_by="admin-1"
        )
        second = self._approved("com.example.two")
        with self.assertRaises(BufferError):
            catalog.promote(
                second, approved_by="reviewer-1", promoted_by="admin-1"
            )
        self.assertTrue(catalog.deactivate(entry_id, actor_id="admin-1"))
        self.assertFalse(catalog.deactivate(entry_id, actor_id="admin-1"))
        self.assertEqual(catalog.list_active(), ())

    def test_owner_scope_isolation(self):
        proposal = self._approved()
        self.catalog.promote(
            proposal, approved_by="reviewer-1", promoted_by="admin-1"
        )
        other = SQLiteCuratedKnowledge(
            str(Path(self.tmp.name) / "other.sqlite3"), owner_id="owner-2"
        )
        self.assertEqual(other.list_active(), ())
        self.assertNotEqual(self.catalog.owner_id, other.owner_id)


if __name__ == "__main__":
    unittest.main()
