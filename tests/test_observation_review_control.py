import tempfile
import unittest
from pathlib import Path

from core.observation_knowledge_store import SQLiteObservationKnowledgeReview
from core.observation_review_control import ObservationKnowledgeReviewService


class ObservationKnowledgeReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteObservationKnowledgeReview(
            str(Path(self.tmp.name) / "review.sqlite3"), owner_id="owner-1"
        )
        self.grants = {
            "operator-1": {
                "observation:review:read",
                "observation:review:decide",
                "observation:review:delete",
            }
        }

        def authorize(actor_id, permission):
            return permission in self.grants.get(actor_id, set())

        self.service = ObservationKnowledgeReviewService(
            self.store, authorize=authorize
        )

    def test_review_requires_authorization_and_records_actor(self):
        proposal = self.store.propose("application", "com.example.editor")
        with self.assertRaises(PermissionError):
            self.service.approve(proposal.proposal_id, actor_id="untrusted")
        approved = self.service.approve(proposal.proposal_id, actor_id="operator-1")
        self.assertEqual(approved.status, "approved")
        events = self.service.list_review_events(
            proposal.proposal_id, actor_id="operator-1"
        )
        self.assertEqual(events[0]["reviewer_id"], "operator-1")

    def test_read_delete_permissions_are_separate(self):
        proposal = self.store.propose("application", "com.example.editor")
        self.grants["reader"] = {"observation:review:read"}
        self.assertEqual(
            len(self.service.list_pending(actor_id="reader")), 1
        )
        with self.assertRaises(PermissionError):
            self.service.reject(proposal.proposal_id, actor_id="reader")
        with self.assertRaises(PermissionError):
            self.service.delete(proposal.proposal_id, actor_id="reader")

    def test_authorizer_failure_fails_closed(self):
        def broken_authorizer(*_args):
            raise RuntimeError("authorization service unavailable")

        service = ObservationKnowledgeReviewService(
            self.store, authorize=broken_authorizer
        )
        with self.assertRaises(PermissionError):
            service.list_pending(actor_id="operator-1")

    def test_invalid_identity_and_authorizer_are_rejected(self):
        with self.assertRaises(ValueError):
            self.service.list_pending(actor_id="")
        with self.assertRaises(TypeError):
            ObservationKnowledgeReviewService(self.store, authorize=None)


    def test_promote_requires_separate_grant_and_preserves_provenance(self):
        from core.curated_knowledge_store import SQLiteCuratedKnowledge

        proposal = self.store.propose("application", "com.example.editor")
        self.service.approve(proposal.proposal_id, actor_id="operator-1")
        catalog = SQLiteCuratedKnowledge(
            str(Path(self.tmp.name) / "curated.sqlite3"), owner_id="owner-1"
        )
        with self.assertRaises(PermissionError):
            self.service.promote_approved(
                proposal.proposal_id, actor_id="operator-1", catalog=catalog
            )
        self.grants["admin-1"] = {"memory:knowledge:promote"}
        entry_id = self.service.promote_approved(
            proposal.proposal_id, actor_id="admin-1", catalog=catalog
        )
        entry, = catalog.list_active()
        self.assertEqual(entry["entry_id"], entry_id)
        self.assertEqual(entry["source_proposal_id"], proposal.proposal_id)
        self.assertEqual(entry["approved_by"], "operator-1")
        self.assertEqual(entry["promoted_by"], "admin-1")
        self.assertEqual(
            self.service.promote_approved(
                proposal.proposal_id, actor_id="admin-1", catalog=catalog
            ),
            entry_id,
        )

    def test_promotion_rejects_catalog_for_another_owner(self):
        from core.curated_knowledge_store import SQLiteCuratedKnowledge

        proposal = self.store.propose("application", "com.example.editor")
        self.service.approve(proposal.proposal_id, actor_id="operator-1")
        self.grants["admin-1"] = {"memory:knowledge:promote"}
        foreign_catalog = SQLiteCuratedKnowledge(
            str(Path(self.tmp.name) / "foreign.sqlite3"), owner_id="owner-2"
        )
        with self.assertRaises(PermissionError):
            self.service.promote_approved(
                proposal.proposal_id, actor_id="admin-1", catalog=foreign_catalog
            )

    def test_pending_proposal_cannot_be_promoted(self):
        from core.curated_knowledge_store import SQLiteCuratedKnowledge

        proposal = self.store.propose("application", "com.example.editor")
        self.grants["admin-1"] = {"memory:knowledge:promote"}
        catalog = SQLiteCuratedKnowledge(
            str(Path(self.tmp.name) / "curated.sqlite3"), owner_id="owner-1"
        )
        with self.assertRaises(PermissionError):
            self.service.promote_approved(
                proposal.proposal_id, actor_id="admin-1", catalog=catalog
            )

    def test_capability_check_requires_read_and_decision_for_capture_review(self):
        self.grants["reader"] = {"observation:review:read"}
        self.assertTrue(self.service.can_review(actor_id="reader"))
        self.assertFalse(self.service.can_review(actor_id="reader", decisions=True))
        self.assertTrue(self.service.can_review(actor_id="operator-1", decisions=True))
        self.assertFalse(self.service.can_review(actor_id="unknown", decisions=True))


if __name__ == "__main__":
    unittest.main()
