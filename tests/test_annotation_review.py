import json
import unittest

from knowledge.annotation import import_annotation_jsonl
from knowledge.annotation_review import (
    AnnotationReviewError,
    calculate_annotation_agreement,
    create_annotation_adjudication,
    create_annotation_review,
)


class AnnotationReviewTests(unittest.TestCase):
    def annotation(self):
        payload = json.dumps({
            "annotation_id": "a1", "task_type": "classification",
            "label": "cat", "asset_id": "img-1",
        }).encode()
        return import_annotation_jsonl(
            payload, source_format="normalized", source_label="test",
            imported_at="2026-11-20T10:00:00Z",
        ).annotations[0]

    def review(self, annotation, reviewer, decision):
        return create_annotation_review(
            annotation, reviewer_label=reviewer, decision=decision,
            confidence=90, rationale="Reviewed against the supplied asset.",
            reviewed_at="2026-11-20T10:00:00Z",
        )

    def test_reviews_are_immutable_digest_bound_and_unverified(self):
        annotation = self.annotation()
        first = self.review(annotation, "reviewer-a", "accept")
        second = self.review(annotation, "reviewer-b", "reject")
        self.assertNotEqual(first.review_sha256, second.review_sha256)
        self.assertEqual(first.annotation_sha256, second.annotation_sha256)
        self.assertFalse(first.verified)
        self.assertEqual(first.provenance, "reviewer_supplied_unverified")
        self.assertEqual(annotation.label, "cat")

    def test_rejects_invalid_decision_confidence_rationale_and_timezone(self):
        annotation = self.annotation()
        with self.assertRaises(AnnotationReviewError):
            self.review(annotation, "reviewer-a", "maybe")
        with self.assertRaises(AnnotationReviewError):
            create_annotation_review(
                annotation, reviewer_label="reviewer-a", decision="accept",
                confidence=True, rationale="ok", reviewed_at="2026-11-20T10:00:00Z",
            )
        with self.assertRaises(AnnotationReviewError):
            create_annotation_review(
                annotation, reviewer_label="reviewer-a", decision="accept",
                confidence=50, rationale="ok", reviewed_at="2026-11-20T10:00:00",
            )

    def test_adjudication_binds_reviews_and_preserves_disagreement(self):
        annotation = self.annotation()
        accepted = self.review(annotation, "reviewer-a", "accept")
        rejected = self.review(annotation, "reviewer-b", "reject")
        result = create_annotation_adjudication(
            annotation, [accepted, rejected], decision="accept",
            adjudicator_label="lead-reviewer",
            rationale="The supplied rubric's inclusion criteria are met.",
            adjudicated_at="2026-11-20T11:00:00+00:00",
        )
        self.assertEqual(result.source_review_sha256, tuple(sorted((accepted.review_sha256, rejected.review_sha256))))
        self.assertFalse(result.verified)
        other = import_annotation_jsonl(
            json.dumps({
                "annotation_id": "a2", "task_type": "classification",
                "label": "dog", "asset_id": "img-2",
            }).encode(), source_format="normalized", source_label="test",
            imported_at="2026-11-20T10:00:00Z",
        ).annotations[0]
        with self.assertRaises(AnnotationReviewError):
            create_annotation_adjudication(
                other, [accepted], decision="accept", adjudicator_label="lead",
                rationale="Wrong annotation binding.", adjudicated_at="2026-11-20T11:00:00Z",
            )

    def test_metrics_report_agreement_and_unresolved_disagreement(self):
        annotation = self.annotation()
        accepted = self.review(annotation, "reviewer-a", "accept")
        rejected = self.review(annotation, "reviewer-b", "reject")
        metrics = calculate_annotation_agreement([annotation], [accepted, rejected])
        self.assertEqual(metrics.multiply_reviewed_count, 1)
        self.assertEqual(metrics.disagreement_count, 1)
        self.assertEqual(metrics.unresolved_disagreement_count, 1)
        self.assertEqual(metrics.exact_agreement_ratio, 0.0)
        adjudication = create_annotation_adjudication(
            annotation, [accepted, rejected], decision="accept",
            adjudicator_label="lead", rationale="Rubric applied.",
            adjudicated_at="2026-11-20T11:00:00Z",
        )
        resolved = calculate_annotation_agreement([annotation], [accepted, rejected], [adjudication])
        self.assertEqual(resolved.unresolved_disagreement_count, 0)
        self.assertEqual(resolved.disagreement_count, 1)

    def test_metrics_reject_stale_or_unknown_reviews(self):
        annotation = self.annotation()
        review = self.review(annotation, "reviewer-a", "accept")
        with self.assertRaises(AnnotationReviewError):
            calculate_annotation_agreement([], [review])


if __name__ == "__main__":
    unittest.main()
