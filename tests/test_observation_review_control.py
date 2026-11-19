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


if __name__ == "__main__":
    unittest.main()
