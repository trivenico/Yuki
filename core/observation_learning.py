"""Local, review-gated learning from explicitly authorized desktop metadata.

This module never calls a model, writes Memory Fabric, executes tools, or
changes prompts/policies. It converts a narrow allowlisted metadata schema
into bounded pending knowledge proposals for a human review surface.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import threading
import time
import uuid
from typing import Optional

from core.observation_event_buffer import ObservationEventBuffer
from core.observation_sources import ObservationEvent


_APP_ID = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}\Z")
_DOMAIN = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z"
)


@dataclass(frozen=True)
class KnowledgeProposal:
    proposal_id: str
    kind: str
    value: str
    summary: str
    fingerprint: str
    status: str
    created_at: float
    reviewed_at: Optional[float] = None


class ObservationKnowledgeReview:
    """Bounded in-memory proposal registry with explicit review transitions."""

    def __init__(self, *, capacity: int = 512, clock=time.time):
        if type(capacity) is not int or not 1 <= capacity <= 10000:
            raise ValueError("capacity must be an integer in 1..10000")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._capacity = capacity
        self._clock = clock
        self._items: dict[str, KnowledgeProposal] = {}
        self._fingerprints: dict[str, str] = {}
        self._lock = threading.RLock()

    def propose(self, kind: str, value: str) -> Optional[KnowledgeProposal]:
        """Create a pending proposal from a strictly validated identifier."""
        if kind == "application":
            valid = isinstance(value, str) and bool(_APP_ID.fullmatch(value))
            summary = f"Observed allowlisted application: {value}" if valid else ""
        elif kind == "browser_domain":
            valid = isinstance(value, str) and bool(_DOMAIN.fullmatch(value))
            summary = f"Observed allowlisted browser domain: {value}" if valid else ""
        else:
            return None
        if not valid:
            return None
        fingerprint = hashlib.sha256(f"{kind}\0{value}".encode("utf-8")).hexdigest()
        with self._lock:
            existing_id = self._fingerprints.get(fingerprint)
            if existing_id is not None:
                return self._items.get(existing_id)
            if len(self._items) >= self._capacity:
                raise BufferError("knowledge proposal review capacity is full")
            item = KnowledgeProposal(
                proposal_id=uuid.uuid4().hex,
                kind=kind,
                value=value,
                summary=summary,
                fingerprint=fingerprint,
                status="pending",
                created_at=float(self._clock()),
            )
            self._items[item.proposal_id] = item
            self._fingerprints[fingerprint] = item.proposal_id
            return item

    def list_pending(self, *, limit: int = 100) -> tuple[KnowledgeProposal, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer in 1..1000")
        with self._lock:
            return tuple(item for item in self._items.values() if item.status == "pending")[:limit]

    def get(self, proposal_id: str) -> Optional[KnowledgeProposal]:
        if not isinstance(proposal_id, str):
            return None
        with self._lock:
            return self._items.get(proposal_id)

    def _review(self, proposal_id: str, decision: str) -> KnowledgeProposal:
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("proposal_id is required")
        with self._lock:
            item = self._items.get(proposal_id)
            if item is None:
                raise KeyError("knowledge proposal not found")
            if item.status != "pending":
                raise RuntimeError("knowledge proposal has already been reviewed")
            updated = KnowledgeProposal(
                proposal_id=item.proposal_id,
                kind=item.kind,
                value=item.value,
                summary=item.summary,
                fingerprint=item.fingerprint,
                status=decision,
                created_at=item.created_at,
                reviewed_at=float(self._clock()),
            )
            self._items[proposal_id] = updated
            return updated

    def approve(self, proposal_id: str) -> KnowledgeProposal:
        """Mark reviewed/approved only; does not install memory or change behavior."""
        return self._review(proposal_id, "approved")

    def reject(self, proposal_id: str) -> KnowledgeProposal:
        return self._review(proposal_id, "rejected")

    def delete(self, proposal_id: str) -> bool:
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("proposal_id is required")
        with self._lock:
            item = self._items.pop(proposal_id, None)
            if item is None:
                return False
            self._fingerprints.pop(item.fingerprint, None)
            return True

    def clear(self) -> int:
        with self._lock:
            count = len(self._items)
            self._items.clear()
            self._fingerprints.clear()
            return count


class ObservationLearningWorker:
    """Bounded synchronous processor; caller schedules it off the UI thread."""

    def __init__(
        self,
        buffer: ObservationEventBuffer,
        review: ObservationKnowledgeReview,
    ):
        if not isinstance(buffer, ObservationEventBuffer):
            raise TypeError("buffer must be an ObservationEventBuffer")
        if not all(callable(getattr(review, name, None)) for name in ("propose", "list_pending", "approve", "reject", "delete")):
            raise TypeError("review must implement the knowledge review store contract")
        self._buffer = buffer
        self._review = review

    def process_batch(self, *, limit: int = 16) -> tuple[KnowledgeProposal, ...]:
        if type(limit) is not int or not 1 <= limit <= min(256, self._buffer.capacity):
            raise ValueError("limit must be within the buffer capacity and 1..256")
        proposals = []
        # Pop incrementally: if review storage fills, only the current event
        # fails visibly and the remaining buffered events stay queued.
        for _ in range(limit):
            event = self._buffer.get_nowait()
            if event is None:
                break
            if not isinstance(event, ObservationEvent):
                continue
            if event.source != "desktop:metadata":
                continue
            metadata = dict(event.metadata)
            if event.category == "app_activity" and set(metadata) == {"application_id"}:
                proposal = self._review.propose("application", metadata["application_id"])
            elif event.category == "browser_domain" and set(metadata) == {"domain"}:
                proposal = self._review.propose("browser_domain", metadata["domain"])
            else:
                # Unknown fields/categories are dropped; never summarize arbitrary
                # observed text or treat it as trusted instructions.
                continue
            if proposal is not None and proposal not in proposals:
                proposals.append(proposal)
        return tuple(proposals)


__all__ = [
    "KnowledgeProposal",
    "ObservationKnowledgeReview",
    "ObservationLearningWorker",
]
