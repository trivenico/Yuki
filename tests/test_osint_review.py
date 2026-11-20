import json
import unittest

from defense.security.osint_graph import parse_osint_jsonl
from defense.security.osint_review import (
    OsintReviewError, create_evidence_review, validate_review_for_graph,
)


class OsintReviewTests(unittest.TestCase):
    def setUp(self):
        row = {
            "evidence_id": "inventory-001",
            "source": "owner_inventory.csv",
            "indicator_type": "domain",
            "value": "example.test",
        }
        self.graph = parse_osint_jsonl(json.dumps(row).encode())
        self.evidence_id = self.graph.evidence[0].evidence_id

    def make_review(self, **overrides):
        values = {
            "disposition": "needs_context",
            "confidence_percent": 35,
            "rationale": "Single owner-supplied observation; corroboration needed.",
            "reviewed_at": "2026-11-20T09:00:00+00:00",
            "reviewer_label": "owner",
        }
        values.update(overrides)
        return create_evidence_review(self.evidence_id, **values)

    def test_creates_digest_bound_human_annotation(self):
        review = self.make_review()
        validate_review_for_graph(review, self.graph)
        self.assertEqual(review.basis, "human_review_only")
        self.assertEqual(len(review.review_digest), 64)
        self.assertFalse(self.graph.evidence[0].verified)

    def test_review_is_immutable_and_does_not_upgrade_evidence(self):
        from dataclasses import FrozenInstanceError
        review = self.make_review()
        with self.assertRaises(FrozenInstanceError):
            review.disposition = "accepted"
        self.assertFalse(self.graph.evidence[0].verified)

    def test_rejects_unknown_evidence_and_digest_tampering(self):
        review = self.make_review()
        other = create_evidence_review("a" * 64, disposition="accepted",
            confidence_percent=90, rationale="Reviewed", reviewed_at="2026-11-20T09:00:00Z")
        with self.assertRaises(OsintReviewError):
            validate_review_for_graph(other, self.graph)
        from dataclasses import replace
        with self.assertRaises(OsintReviewError):
            validate_review_for_graph(replace(review, rationale="tampered"), self.graph)

    def test_rejects_invalid_confidence_disposition_and_timestamp(self):
        for changes in (
            {"confidence_percent": True}, {"confidence_percent": 101},
            {"disposition": "verified"}, {"reviewed_at": "yesterday"},
            {"reviewed_at": "2026-11-20T09:00:00"}, {"rationale": " "},
        ):
            with self.subTest(changes=changes), self.assertRaises(OsintReviewError):
                self.make_review(**changes)


if __name__ == "__main__":
    unittest.main()
