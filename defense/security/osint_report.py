"""Deterministic, provenance-preserving OSINT investigation snapshots."""
from __future__ import annotations

import json

from defense.security.osint_graph import OsintGraph, OsintImportError
from defense.security.osint_review import (
    OsintEvidenceReview, OsintReviewError, validate_review_for_graph,
)
from defense.security.osint_audit import (
    OsintAuditError, OsintAuditEvent, verify_audit_history,
)

_MAX_REPORT_BYTES = 4 * 1024 * 1024
_MAX_REVIEWS = 10_000


class OsintReportError(ValueError):
    """Raised when an investigation snapshot is invalid or exceeds limits."""


def _review_record(review: OsintEvidenceReview) -> dict:
    return {
        "evidence_id": review.evidence_id,
        "disposition": review.disposition,
        "confidence_percent": review.confidence_percent,
        "rationale": review.rationale,
        "reviewed_at": review.reviewed_at,
        "reviewer_label": review.reviewer_label,
        "basis": review.basis,
        "review_digest": review.review_digest,
    }


def _audit_record(event: OsintAuditEvent) -> dict:
    return {
        "sequence": event.sequence,
        "event_type": event.event_type,
        "evidence_id": event.evidence_id,
        "actor_label": event.actor_label,
        "occurred_at": event.occurred_at,
        "details_digest": event.details_digest,
        "previous_digest": event.previous_digest,
        "event_digest": event.event_digest,
    }


def build_investigation_report(
    graph: OsintGraph,
    *,
    reviews: tuple[OsintEvidenceReview, ...] = (),
    audit_history: tuple[OsintAuditEvent, ...] = (),
    max_bytes: int = _MAX_REPORT_BYTES,
) -> bytes:
    """Build canonical JSON bytes from validated graph, human reviews and audit.

    This is an export snapshot, not an authenticated or independently verified
    report. Evidence remains owner-supplied and unverified. No network access,
    enrichment, attribution, or filesystem writes occur.
    """
    if type(graph) is not OsintGraph:
        raise OsintReportError("an OSINT graph is required")
    if type(reviews) is not tuple or len(reviews) > _MAX_REVIEWS:
        raise OsintReportError("reviews must be a bounded immutable tuple")
    if type(audit_history) is not tuple:
        raise OsintReportError("audit history must be an immutable tuple")
    if type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_REPORT_BYTES:
        raise OsintReportError("invalid report byte limit")

    try:
        evidence_ids = {item.evidence_id for item in graph.evidence}
        review_map = {}
        for review in reviews:
            validate_review_for_graph(review, graph)
            if review.review_digest in review_map:
                raise OsintReportError("duplicate review")
            review_map[review.review_digest] = review
        verify_audit_history(audit_history)
    except (OsintImportError, OsintReviewError, OsintAuditError) as exc:
        raise OsintReportError("investigation inputs failed validation") from exc

    if any(item.evidence_id not in evidence_ids for item in graph.evidence):
        raise OsintReportError("graph contains invalid evidence reference")
    for event in audit_history:
        review = review_map.get(event.details_digest)
        if (review is None or event.evidence_id != review.evidence_id
                or event.actor_label != review.reviewer_label
                or event.occurred_at != review.reviewed_at):
            raise OsintReportError("audit event has no matching included review")

    evidence = [
        {
            "evidence_id": item.evidence_id,
            "source_label": item.source_label,
            "observed_at": item.observed_at,
            "provenance": item.provenance,
            "verified": item.verified,
        }
        for item in graph.evidence
    ]
    nodes = [
        {"indicator_type": item.indicator_type, "value": item.value}
        for item in graph.nodes
    ]
    edges = [
        {
            "source": {"indicator_type": item.source.indicator_type, "value": item.source.value},
            "relationship": item.relationship,
            "target": {"indicator_type": item.target.indicator_type, "value": item.target.value},
            "evidence_ids": list(item.evidence_ids),
        }
        for item in graph.edges
    ]
    observations = [
        {
            "indicator_type": item.node.indicator_type,
            "value": item.node.value,
            "evidence_id": item.evidence_id,
        }
        for item in graph.observations
    ]
    report = {
        "schema_version": 1,
        "report_type": "yuki_osint_investigation_snapshot",
        "trust_boundary": {
            "evidence_provenance": "owner_supplied_unverified",
            "verified_externally": False,
            "human_reviews_are_not_source_verification": True,
            "audit_authenticates_actor": False,
            "collection_mode": "offline_user_supplied_material",
        },
        "graph": {
            "nodes": nodes,
            "edges": edges,
            "evidence": evidence,
            "observations": observations,
            "skipped_records": graph.skipped_records,
        },
        "reviews": [_review_record(item) for item in sorted(
            reviews, key=lambda value: (value.evidence_id, value.reviewed_at, value.review_digest)
        )],
        "audit_history": [_audit_record(item) for item in audit_history],
    }
    output = json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(output) > max_bytes:
        raise OsintReportError("investigation report exceeds byte limit")
    return output
