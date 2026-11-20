"""Immutable human review, adjudication, and agreement metrics for annotations."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from typing import Iterable

from knowledge.annotation import Annotation

MAX_RATIONALE_CHARS = 2000
MAX_REVIEWER_CHARS = 128
MAX_REVIEWS = 100_000
DECISIONS = frozenset({"accept", "reject", "needs_context"})


class AnnotationReviewError(ValueError):
    """Raised when a review or adjudication record is invalid."""


@dataclass(frozen=True, slots=True)
class AnnotationReview:
    annotation_id: str
    annotation_sha256: str
    reviewer_label: str
    decision: str
    confidence: int
    rationale: str
    reviewed_at: str
    review_sha256: str
    provenance: str = "reviewer_supplied_unverified"
    verified: bool = False


@dataclass(frozen=True, slots=True)
class AnnotationAdjudication:
    annotation_id: str
    annotation_sha256: str
    source_review_sha256: tuple[str, ...]
    decision: str
    adjudicator_label: str
    rationale: str
    adjudicated_at: str
    adjudication_sha256: str
    provenance: str = "human_adjudication_unverified"
    verified: bool = False


@dataclass(frozen=True, slots=True)
class AnnotationAgreementMetrics:
    annotation_count: int
    multiply_reviewed_count: int
    unanimous_count: int
    disagreement_count: int
    exact_agreement_ratio: float | None
    unresolved_disagreement_count: int
    provenance: str = "reviewer_supplied_unverified"
    verified: bool = False


def _canonical_digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _timestamp(value: str, field: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise AnnotationReviewError(f"invalid {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnnotationReviewError(f"invalid {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnnotationReviewError(f"{field} must include a timezone")
    return value


def annotation_digest(annotation: Annotation) -> str:
    if not isinstance(annotation, Annotation):
        raise AnnotationReviewError("annotation must be an Annotation")
    return _canonical_digest(asdict(annotation))


def create_annotation_review(
    annotation: Annotation,
    *,
    reviewer_label: str,
    decision: str,
    confidence: int,
    rationale: str,
    reviewed_at: str,
) -> AnnotationReview:
    """Create a digest-bound review; reviewer_label is not authenticated identity."""
    if not isinstance(reviewer_label, str) or not reviewer_label.strip() or len(reviewer_label) > MAX_REVIEWER_CHARS:
        raise AnnotationReviewError("invalid reviewer_label")
    if not isinstance(decision, str) or decision not in DECISIONS:
        raise AnnotationReviewError("unsupported review decision")
    if isinstance(confidence, bool) or not isinstance(confidence, int) or not 0 <= confidence <= 100:
        raise AnnotationReviewError("confidence must be an integer from 0 to 100")
    if not isinstance(rationale, str) or len(rationale) > MAX_RATIONALE_CHARS or not rationale.strip():
        raise AnnotationReviewError("invalid rationale")
    _timestamp(reviewed_at, "reviewed_at")
    annotation_sha = annotation_digest(annotation)
    payload = {
        "annotation_id": annotation.annotation_id,
        "annotation_sha256": annotation_sha,
        "reviewer_label": reviewer_label,
        "decision": decision,
        "confidence": confidence,
        "rationale": rationale,
        "reviewed_at": reviewed_at,
        "provenance": "reviewer_supplied_unverified",
        "verified": False,
    }
    return AnnotationReview(
        annotation_id=annotation.annotation_id,
        annotation_sha256=annotation_sha,
        reviewer_label=reviewer_label,
        decision=decision,
        confidence=confidence,
        rationale=rationale,
        reviewed_at=reviewed_at,
        review_sha256=_canonical_digest(payload),
    )


def create_annotation_adjudication(
    annotation: Annotation,
    reviews: Iterable[AnnotationReview],
    *,
    decision: str,
    adjudicator_label: str,
    rationale: str,
    adjudicated_at: str,
) -> AnnotationAdjudication:
    """Record an explicit human resolution without modifying source reviews."""
    if not isinstance(decision, str) or decision not in DECISIONS:
        raise AnnotationReviewError("unsupported adjudication decision")
    if not isinstance(adjudicator_label, str) or not adjudicator_label.strip() or len(adjudicator_label) > MAX_REVIEWER_CHARS:
        raise AnnotationReviewError("invalid adjudicator_label")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > MAX_RATIONALE_CHARS:
        raise AnnotationReviewError("invalid rationale")
    _timestamp(adjudicated_at, "adjudicated_at")
    if not isinstance(annotation, Annotation):
        raise AnnotationReviewError("annotation must be an Annotation")
    items = tuple(reviews)
    if not items or len(items) > MAX_REVIEWS:
        raise AnnotationReviewError("adjudication requires a bounded, non-empty review set")
    if any(not isinstance(item, AnnotationReview) for item in items):
        raise AnnotationReviewError("reviews must contain AnnotationReview records")
    if len({item.review_sha256 for item in items}) != len(items):
        raise AnnotationReviewError("duplicate source review")
    annotation_sha = annotation_digest(annotation)
    for item in items:
        if item.annotation_id != annotation.annotation_id or item.annotation_sha256 != annotation_sha:
            raise AnnotationReviewError("source review does not match the adjudicated annotation")
        if not _valid_review(item) or item.annotation_sha256 != annotation_sha:
            raise AnnotationReviewError("invalid or improperly verified source review")
    review_digests = tuple(sorted(item.review_sha256 for item in items))
    payload = {
        "annotation_id": annotation.annotation_id,
        "annotation_sha256": annotation_sha,
        "source_review_sha256": review_digests,
        "decision": decision,
        "adjudicator_label": adjudicator_label,
        "rationale": rationale,
        "adjudicated_at": adjudicated_at,
        "provenance": "human_adjudication_unverified",
        "verified": False,
    }
    return AnnotationAdjudication(
        annotation_id=annotation.annotation_id,
        annotation_sha256=annotation_sha,
        source_review_sha256=review_digests,
        decision=decision,
        adjudicator_label=adjudicator_label,
        rationale=rationale,
        adjudicated_at=adjudicated_at,
        adjudication_sha256=_canonical_digest(payload),
    )


def calculate_annotation_agreement(
    annotations: Iterable[Annotation],
    reviews: Iterable[AnnotationReview],
    adjudications: Iterable[AnnotationAdjudication] = (),
) -> AnnotationAgreementMetrics:
    """Compute unweighted exact decision agreement across multiply reviewed annotations."""
    annotation_items = tuple(annotations)
    review_items = tuple(reviews)
    adjudication_items = tuple(adjudications)
    if len(annotation_items) > MAX_REVIEWS or len(review_items) > MAX_REVIEWS or len(adjudication_items) > MAX_REVIEWS:
        raise AnnotationReviewError("agreement input exceeds record limit")
    by_id = {item.annotation_id: item for item in annotation_items}
    if len(by_id) != len(annotation_items):
        raise AnnotationReviewError("duplicate annotation IDs")
    grouped: dict[str, list[AnnotationReview]] = defaultdict(list)
    for review in review_items:
        if not isinstance(review, AnnotationReview) or review.annotation_id not in by_id:
            raise AnnotationReviewError("review references an unknown annotation")
        annotation = by_id[review.annotation_id]
        if review.annotation_sha256 != annotation_digest(annotation):
            raise AnnotationReviewError("review digest does not match current annotation")
        if not _valid_review(review):
            raise AnnotationReviewError("invalid or improperly verified review")
        if any(existing.reviewer_label == review.reviewer_label for existing in grouped[review.annotation_id]):
            raise AnnotationReviewError("only one review per reviewer label and annotation is allowed")
        grouped[review.annotation_id].append(review)
    if len(review_items) != len({item.review_sha256 for item in review_items}):
        raise AnnotationReviewError("duplicate review digest")
    adjudicated = set()
    for record in adjudication_items:
        if not isinstance(record, AnnotationAdjudication) or record.annotation_id not in by_id:
            raise AnnotationReviewError("adjudication references an unknown annotation")
        annotation = by_id[record.annotation_id]
        if record.annotation_sha256 != annotation_digest(annotation) or record.decision not in DECISIONS or record.verified or not _valid_adjudication(record):
            raise AnnotationReviewError("invalid adjudication binding")
        known = {item.review_sha256 for item in grouped[record.annotation_id]}
        if not set(record.source_review_sha256) <= known or not record.source_review_sha256:
            raise AnnotationReviewError("adjudication references unknown source reviews")
        adjudicated.add(record.annotation_id)
    multi = [items for items in grouped.values() if len(items) >= 2]
    unanimous = sum(len({item.decision for item in items}) == 1 for items in multi)
    disagreements = len(multi) - unanimous
    ratio = (sum(len({item.decision for item in items}) == 1 for items in multi) / len(multi)) if multi else None
    unresolved = sum(
        len({item.decision for item in items}) > 1 and annotation_id not in adjudicated
        for annotation_id, items in grouped.items() if len(items) >= 2
    )
    return AnnotationAgreementMetrics(
        annotation_count=len(annotation_items),
        multiply_reviewed_count=len(multi),
        unanimous_count=unanimous,
        disagreement_count=disagreements,
        exact_agreement_ratio=ratio,
        unresolved_disagreement_count=unresolved,
    )

def _valid_review(item: AnnotationReview) -> bool:
    if item.provenance != "reviewer_supplied_unverified" or item.verified or item.decision not in DECISIONS:
        return False
    if not isinstance(item.confidence, int) or isinstance(item.confidence, bool) or not 0 <= item.confidence <= 100:
        return False
    if not isinstance(item.reviewer_label, str) or not item.reviewer_label.strip() or len(item.reviewer_label) > MAX_REVIEWER_CHARS:
        return False
    if not isinstance(item.rationale, str) or not item.rationale.strip() or len(item.rationale) > MAX_RATIONALE_CHARS:
        return False
    try:
        _timestamp(item.reviewed_at, "reviewed_at")
    except AnnotationReviewError:
        return False
    payload = {
        "annotation_id": item.annotation_id, "annotation_sha256": item.annotation_sha256,
        "reviewer_label": item.reviewer_label, "decision": item.decision,
        "confidence": item.confidence, "rationale": item.rationale,
        "reviewed_at": item.reviewed_at, "provenance": item.provenance, "verified": False,
    }
    return item.review_sha256 == _canonical_digest(payload)


def _valid_adjudication(item: AnnotationAdjudication) -> bool:
    if item.provenance != "human_adjudication_unverified" or item.verified:
        return False
    if not isinstance(item.decision, str) or item.decision not in DECISIONS:
        return False
    if not isinstance(item.adjudicator_label, str) or not item.adjudicator_label.strip() or len(item.adjudicator_label) > MAX_REVIEWER_CHARS:
        return False
    if not isinstance(item.rationale, str) or not item.rationale.strip() or len(item.rationale) > MAX_RATIONALE_CHARS:
        return False
    try:
        _timestamp(item.adjudicated_at, "adjudicated_at")
    except AnnotationReviewError:
        return False
    if not item.source_review_sha256 or tuple(sorted(set(item.source_review_sha256))) != item.source_review_sha256:
        return False
    payload = {
        "annotation_id": item.annotation_id, "annotation_sha256": item.annotation_sha256,
        "source_review_sha256": item.source_review_sha256, "decision": item.decision,
        "adjudicator_label": item.adjudicator_label, "rationale": item.rationale,
        "adjudicated_at": item.adjudicated_at, "provenance": item.provenance, "verified": False,
    }
    return item.adjudication_sha256 == _canonical_digest(payload)
