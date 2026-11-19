import tempfile
import unittest
from pathlib import Path

from core.curated_knowledge_store import SQLiteCuratedKnowledge
from core.curated_knowledge_provider import CuratedKnowledgeProvider
from core.observation_knowledge_store import SQLiteObservationKnowledgeReview
from core.observation_review_control import ObservationKnowledgeReviewService


class CuratedKnowledgeProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.review_store = SQLiteObservationKnowledgeReview(
            str(Path(self.tmp.name) / "review.sqlite3"), owner_id="owner-a"
        )
        self.catalog = SQLiteCuratedKnowledge(
            str(Path(self.tmp.name) / "catalog.sqlite3"), owner_id="owner-a"
        )
        self.grants = {"admin": {"memory:knowledge:read", "memory:knowledge:promote",
                                 "memory:knowledge:deactivate"}}
        self.service = ObservationKnowledgeReviewService(
            self.review_store,
            authorize=lambda actor, permission: permission in self.grants.get(actor, set()),
        )

    def add_approved(self, value):
        proposal = self.review_store.propose("application", value)
        self.review_store.approve(proposal.proposal_id, reviewer_id="reviewer")
        return self.service.promote_approved(
            proposal.proposal_id, actor_id="admin", catalog=self.catalog
        )

    def provider(self, actor="admin"):
        return CuratedKnowledgeProvider(
            self.service, self.catalog, actor_id_provider=lambda owner: actor
        )

    def test_retrieves_relevant_records_with_provenance_and_bounded_results(self):
        first = self.add_approved("com.example.editor")
        self.add_approved("com.example.browser")
        result = self.provider()("owner-a", "Help me use my code editor")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["entry_id"], first)
        self.assertEqual(result[0]["approved_by"], "reviewer")

    def test_live_permission_revocation_applies_without_cache(self):
        self.add_approved("com.example.editor", "A desktop code editor")
        provider = self.provider()
        self.assertEqual(len(provider("owner-a", "code editor")), 1)
        self.grants["admin"].remove("memory:knowledge:read")
        with self.assertRaises(PermissionError):
            provider("owner-a", "code editor")

    def test_deactivation_is_visible_on_next_retrieval(self):
        entry_id = self.add_approved("com.example.editor", "A desktop code editor")
        provider = self.provider()
        self.assertEqual(len(provider("owner-a", "code editor")), 1)
        self.service.deactivate_curated(entry_id, actor_id="admin", catalog=self.catalog)
        self.assertEqual(provider("owner-a", "code editor"), ())

    def test_owner_scope_is_enforced_before_catalog_access(self):
        provider = self.provider()
        with self.assertRaises(PermissionError):
            provider("owner-b", "code editor")

    def test_provider_validates_inputs_and_configuration(self):
        with self.assertRaises(ValueError):
            self.provider()("", "query")
        with self.assertRaises(ValueError):
            self.provider()("owner-a", "")
        with self.assertRaises(ValueError):
            CuratedKnowledgeProvider(
                self.service, self.catalog,
                actor_id_provider=lambda owner: "admin", max_results=6,
            )


if __name__ == "__main__":
    unittest.main()
