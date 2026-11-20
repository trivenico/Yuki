import unittest
from dataclasses import replace

from defense.security.osint_review import create_evidence_review
from defense.security.osint_audit import (
    OsintAuditError, append_review_event, verify_audit_history,
)


class OsintAuditTests(unittest.TestCase):
    def review(self, evidence_id="a" * 64, rationale="Reviewed source"):
        return create_evidence_review(
            evidence_id, disposition="needs_context", confidence_percent=25,
            rationale=rationale, reviewed_at="2026-11-20T10:00:00Z",
            reviewer_label="owner",
        )

    def test_appends_hash_chained_events_without_mutating_prior_history(self):
        empty = ()
        first = append_review_event(empty, self.review())
        second = append_review_event(first, self.review("b" * 64))
        self.assertEqual(empty, ())
        self.assertEqual(len(first), 1)
        self.assertEqual(second[1].previous_digest, second[0].event_digest)
        verify_audit_history(second)

    def test_rejects_tampering_reordering_and_duplicate_reviews(self):
        history = append_review_event((), self.review())
        with self.assertRaises(OsintAuditError):
            verify_audit_history((replace(history[0], actor_label="forged"),))
        with self.assertRaises(OsintAuditError):
            verify_audit_history((replace(history[0], sequence=2),))
        with self.assertRaises(OsintAuditError):
            append_review_event(history, self.review())

    def test_rejects_mutable_or_oversized_history(self):
        with self.assertRaises(OsintAuditError):
            verify_audit_history([])
        with self.assertRaises(OsintAuditError):
            append_review_event([], self.review())


if __name__ == "__main__":
    unittest.main()
