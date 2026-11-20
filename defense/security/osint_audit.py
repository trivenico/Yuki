"""Append-only, hash-chained audit records for OSINT investigation reviews.

This is an in-memory serialization primitive, not durable storage or a signature
system. Persisted records still require protected storage and authenticated actors.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable

from defense.security.osint_review import OsintEvidenceReview

_MAX_EVENTS = 10_000
_MAX_CHAIN_BYTES = 4 * 1024 * 1024


class OsintAuditError(ValueError):
    """Raised for malformed or inconsistent investigation audit history."""


@dataclass(frozen=True)
class OsintAuditEvent:
    sequence: int
    event_type: str
    evidence_id: str
    actor_label: str
    occurred_at: str
    details_digest: str
    previous_digest: str
    event_digest: str


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(body: dict) -> str:
    return hashlib.sha256(_canonical(body)).hexdigest()


def append_review_event(
    history: tuple[OsintAuditEvent, ...],
    review: OsintEvidenceReview,
) -> tuple[OsintAuditEvent, ...]:
    """Return a new audit tuple; existing events are never mutated."""
    if type(history) is not tuple or len(history) >= _MAX_EVENTS:
        raise OsintAuditError("invalid or full audit history")
    if type(review) is not OsintEvidenceReview:
        raise OsintAuditError("an evidence review is required")
    verify_audit_history(history)
    if any(event.evidence_id == review.evidence_id and event.details_digest == review.review_digest
           for event in history):
        raise OsintAuditError("duplicate review event")
    previous = history[-1].event_digest if history else "0" * 64
    body = {
        "sequence": len(history) + 1,
        "event_type": "evidence_review",
        "evidence_id": review.evidence_id,
        "actor_label": review.reviewer_label,
        "occurred_at": review.reviewed_at,
        "details_digest": review.review_digest,
        "previous_digest": previous,
    }
    event = OsintAuditEvent(**body, event_digest=_digest(body))
    candidate = history + (event,)
    if sum(len(_canonical(_event_body(item))) + 64 for item in candidate) > _MAX_CHAIN_BYTES:
        raise OsintAuditError("audit history exceeds size limit")
    return candidate


def _event_body(event: OsintAuditEvent) -> dict:
    return {
        "sequence": event.sequence,
        "event_type": event.event_type,
        "evidence_id": event.evidence_id,
        "actor_label": event.actor_label,
        "occurred_at": event.occurred_at,
        "details_digest": event.details_digest,
        "previous_digest": event.previous_digest,
    }


def verify_audit_history(history: Iterable[OsintAuditEvent]) -> None:
    """Verify sequence, link and digest integrity; does not authenticate the actor."""
    if type(history) is not tuple or len(history) > _MAX_EVENTS:
        raise OsintAuditError("audit history must be a bounded immutable tuple")
    previous = "0" * 64
    total = 0
    for index, event in enumerate(history, 1):
        if type(event) is not OsintAuditEvent or event.sequence != index:
            raise OsintAuditError("invalid audit event sequence")
        if event.event_type != "evidence_review":
            raise OsintAuditError("unsupported audit event type")
        if not all(isinstance(value, str) and value for value in (
            event.evidence_id, event.actor_label, event.occurred_at
        )):
            raise OsintAuditError("invalid audit event fields")
        if any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
               for value in (event.evidence_id, event.details_digest, event.previous_digest, event.event_digest)):
            raise OsintAuditError("invalid audit digest")
        if event.previous_digest != previous or event.event_digest != _digest(_event_body(event)):
            raise OsintAuditError("audit chain integrity failure")
        total += len(_canonical(_event_body(event))) + 64
        if total > _MAX_CHAIN_BYTES:
            raise OsintAuditError("audit history exceeds size limit")
        previous = event.event_digest
