"""Human-authored OSINT evidence review annotations.

Reviews are immutable metadata. They never alter source evidence, provenance,
or the verification state of an observation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from defense.security.osint_graph import OsintGraph


class OsintReviewError(ValueError):
    """Raised when a human evidence-review annotation is invalid."""


@dataclass(frozen=True)
class OsintEvidenceReview:
    evidence_id: str
    disposition: str
    confidence_percent: int
    rationale: str
    reviewed_at: str
    reviewer_label: str
    basis: str
    review_digest: str


def _bounded_text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise OsintReviewError(f"invalid {field}")
    value = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise OsintReviewError(f"invalid {field}")
    return value


def create_evidence_review(
    evidence_id: str,
    *,
    disposition: str,
    confidence_percent: int,
    rationale: str,
    reviewed_at: str,
    reviewer_label: str = "owner",
) -> OsintEvidenceReview:
    """Create a digest-bound human annotation without upgrading evidence trust."""
    if not isinstance(evidence_id, str) or len(evidence_id) != 64 or any(
        char not in "0123456789abcdef" for char in evidence_id
    ):
        raise OsintReviewError("invalid evidence identifier")
    if disposition not in {"accepted", "rejected", "needs_context"}:
        raise OsintReviewError("unsupported review disposition")
    if type(confidence_percent) is not int or not 0 <= confidence_percent <= 100:
        raise OsintReviewError("confidence must be an integer from 0 to 100")
    reason = _bounded_text(rationale, "review rationale", 512)
    reviewer = _bounded_text(reviewer_label, "reviewer label", 64)
    timestamp = _bounded_text(reviewed_at, "review timestamp", 40)
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OsintReviewError("review timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise OsintReviewError("review timestamp must include a timezone")

    body = {
        "evidence_id": evidence_id,
        "disposition": disposition,
        "confidence_percent": confidence_percent,
        "rationale": reason,
        "reviewed_at": timestamp,
        "reviewer_label": reviewer,
        "basis": "human_review_only",
    }
    canonical = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return OsintEvidenceReview(**body, review_digest=digest)


def validate_review_for_graph(review: OsintEvidenceReview, graph: OsintGraph) -> None:
    """Require the reviewed evidence to exist; do not mutate the graph or its trust."""
    if type(review) is not OsintEvidenceReview or type(graph) is not OsintGraph:
        raise OsintReviewError("review and graph types are required")
    if review.evidence_id not in {item.evidence_id for item in graph.evidence}:
        raise OsintReviewError("review references unknown evidence")
    expected = create_evidence_review(
        review.evidence_id,
        disposition=review.disposition,
        confidence_percent=review.confidence_percent,
        rationale=review.rationale,
        reviewed_at=review.reviewed_at,
        reviewer_label=review.reviewer_label,
    )
    if review.basis != "human_review_only" or review.review_digest != expected.review_digest:
        raise OsintReviewError("review digest or basis is invalid")
