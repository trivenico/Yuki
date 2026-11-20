import json
import unittest
from dataclasses import replace

from defense.security.osint_graph import parse_osint_jsonl
from defense.security.osint_review import create_evidence_review
from defense.security.osint_audit import append_review_event
from defense.security.osint_report import OsintReportError, build_investigation_report


class OsintReportTests(unittest.TestCase):
    def setUp(self):
        row = {
            "evidence_id": "inventory-001",
            "source": "owner_inventory.csv",
            "indicator_type": "domain",
            "value": "Example.test",
        }
        self.graph = parse_osint_jsonl(json.dumps(row).encode())
        self.evidence_id = self.graph.evidence[0].evidence_id
        self.review = create_evidence_review(
            self.evidence_id, disposition="needs_context", confidence_percent=40,
            rationale="Needs independent corroboration.",
            reviewed_at="2026-11-20T10:00:00Z", reviewer_label="owner",
        )

    def test_exports_deterministic_provenance_preserving_snapshot(self):
        history = append_review_event((), self.review)
        first = build_investigation_report(self.graph, reviews=(self.review,), audit_history=history)
        second = build_investigation_report(self.graph, reviews=(self.review,), audit_history=history)
        self.assertEqual(first, second)
        report = json.loads(first)
        self.assertEqual(report["schema_version"], 1)
        self.assertFalse(report["trust_boundary"]["verified_externally"])
        self.assertFalse(report["trust_boundary"]["audit_authenticates_actor"])
        self.assertFalse(report["graph"]["evidence"][0]["verified"])
        self.assertEqual(report["reviews"][0]["review_digest"], self.review.review_digest)
        self.assertEqual(report["audit_history"][0]["details_digest"], self.review.review_digest)

    def test_rejects_review_for_unknown_evidence_and_duplicate_reviews(self):
        unknown = create_evidence_review(
            "a" * 64, disposition="accepted", confidence_percent=90,
            rationale="Reviewed", reviewed_at="2026-11-20T10:00:00Z",
        )
        with self.assertRaises(OsintReportError):
            build_investigation_report(self.graph, reviews=(unknown,))
        with self.assertRaises(OsintReportError):
            build_investigation_report(self.graph, reviews=(self.review, self.review))

    def test_rejects_audit_event_without_matching_review(self):
        history = append_review_event((), self.review)
        with self.assertRaises(OsintReportError):
            build_investigation_report(self.graph, audit_history=history)

    def test_rejects_tampered_review_and_audit(self):
        history = append_review_event((), self.review)
        tampered = replace(self.review, rationale="forged")
        with self.assertRaises(OsintReportError):
            build_investigation_report(self.graph, reviews=(tampered,), audit_history=history)

    def test_rejects_mutable_inputs_and_invalid_or_exceeded_limits(self):
        with self.assertRaises(OsintReportError):
            build_investigation_report(self.graph, reviews=[self.review])
        with self.assertRaises(OsintReportError):
            build_investigation_report(self.graph, max_bytes=0)
        with self.assertRaises(OsintReportError):
            build_investigation_report(self.graph, reviews=(self.review,), max_bytes=1)


if __name__ == "__main__":
    unittest.main()
