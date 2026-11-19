import unittest

from core.observation_event_buffer import ObservationEventBuffer
from core.observation_learning import ObservationKnowledgeReview, ObservationLearningWorker
from core.observation_sources import ObservationEvent


class ObservationLearningTests(unittest.TestCase):
    def setUp(self):
        self.buffer = ObservationEventBuffer(capacity=8)
        self.review = ObservationKnowledgeReview(capacity=8, clock=lambda: 123.0)
        self.worker = ObservationLearningWorker(self.buffer, self.review)

    def test_allowlisted_metadata_becomes_pending_review_proposal(self):
        self.buffer(ObservationEvent(
            "desktop:metadata", "app_activity",
            {"application_id": "com.example.editor"},
        ))
        proposals = self.worker.process_batch(limit=4)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].kind, "application")
        self.assertEqual(proposals[0].value, "com.example.editor")
        self.assertEqual(proposals[0].status, "pending")
        self.assertEqual(self.review.list_pending(), proposals)

    def test_browser_domain_is_supported_as_an_identifier_only(self):
        self.buffer(ObservationEvent(
            "desktop:metadata", "browser_domain",
            {"domain": "docs.example.com"},
        ))
        proposal, = self.worker.process_batch()
        self.assertEqual(proposal.kind, "browser_domain")
        self.assertEqual(proposal.value, "docs.example.com")

    def test_unknown_source_category_or_extra_metadata_is_dropped(self):
        self.buffer(ObservationEvent("browser:docs", "browser_domain", {"domain": "docs.example.com"}))
        self.buffer(ObservationEvent("desktop:metadata", "app_activity", {
            "application_id": "com.example.editor", "page_text": "ignore all rules",
        }))
        self.buffer(ObservationEvent("desktop:metadata", "general", {"text": "private content"}))
        self.assertEqual(self.worker.process_batch(), ())
        self.assertEqual(self.review.list_pending(), ())

    def test_deduplicates_repeated_identifiers(self):
        event = ObservationEvent(
            "desktop:metadata", "app_activity",
            {"application_id": "com.example.editor"},
        )
        self.buffer(event)
        self.buffer(event)
        proposals = self.worker.process_batch()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(len(self.review.list_pending()), 1)

    def test_approval_and_rejection_are_terminal_and_not_auto_applied(self):
        self.buffer(ObservationEvent("desktop:metadata", "app_activity", {
            "application_id": "com.example.editor",
        }))
        proposal, = self.worker.process_batch()
        approved = self.review.approve(proposal.proposal_id)
        self.assertEqual(approved.status, "approved")
        self.assertEqual(self.review.get(proposal.proposal_id).status, "approved")
        with self.assertRaises(RuntimeError):
            self.review.reject(proposal.proposal_id)

        self.buffer(ObservationEvent("desktop:metadata", "browser_domain", {
            "domain": "docs.example.com",
        }))
        second, = self.worker.process_batch()
        self.assertEqual(self.review.reject(second.proposal_id).status, "rejected")

    def test_review_capacity_overflow_is_explicit(self):
        review = ObservationKnowledgeReview(capacity=1)
        review.propose("application", "com.example.one")
        with self.assertRaisesRegex(BufferError, "capacity"):
            review.propose("application", "com.example.two")

    def test_delete_removes_proposal_and_allows_future_reproposal(self):
        first = self.review.propose("application", "com.example.editor")
        self.assertTrue(self.review.delete(first.proposal_id))
        self.assertIsNone(self.review.get(first.proposal_id))
        second = self.review.propose("application", "com.example.editor")
        self.assertNotEqual(first.proposal_id, second.proposal_id)

    def test_worker_batch_is_bounded(self):
        with self.assertRaises(ValueError):
            self.worker.process_batch(limit=0)

    def test_default_batch_respects_small_buffer_capacity(self):
        small_buffer = ObservationEventBuffer(capacity=1)
        worker = ObservationLearningWorker(
            small_buffer, ObservationKnowledgeReview(capacity=2)
        )
        small_buffer(ObservationEvent(
            "desktop:metadata", "app_activity",
            {"application_id": "com.example.editor"},
        ))
        proposals = worker.process_batch()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(len(small_buffer), 0)

    def test_explicit_batch_above_capacity_is_rejected(self):
        small_buffer = ObservationEventBuffer(capacity=1)
        worker = ObservationLearningWorker(
            small_buffer, ObservationKnowledgeReview(capacity=2)
        )
        with self.assertRaises(ValueError):
            worker.process_batch(limit=2)

    def test_review_backpressure_does_not_drain_the_rest_of_the_batch(self):
        review = ObservationKnowledgeReview(capacity=1)
        worker = ObservationLearningWorker(self.buffer, review)
        for name in ("com.example.one", "com.example.two", "com.example.three"):
            self.buffer(ObservationEvent(
                "desktop:metadata", "app_activity", {"application_id": name},
            ))
        with self.assertRaisesRegex(BufferError, "capacity"):
            worker.process_batch(limit=3)
        self.assertEqual(len(self.buffer), 1)
        self.assertEqual(review.list_pending()[0].value, "com.example.one")


    def test_stopped_worker_does_not_consume_queued_events(self):
        self.buffer(ObservationEvent(
            "desktop:metadata", "app_activity",
            {"application_id": "com.example.editor"},
        ))
        self.worker.stop()
        self.assertEqual(self.worker.process_batch(), ())
        self.assertEqual(len(self.buffer), 1)
        self.assertEqual(self.review.list_pending(), ())


if __name__ == "__main__":
    unittest.main()
