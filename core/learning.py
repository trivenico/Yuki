"""Consent-gated foreground and background learning proposals.

Candidate review is durable when a LearningQueueStore is injected. Lessons
enter owner memory only after explicit approval and a fresh consent check.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4
from typing import Any, Callable


@dataclass(frozen=True)
class LearningCandidate:
    candidate_id: str
    owner_id: str
    source: str
    lesson: str
    evidence: str
    created_at: str


class LearningCoordinator:
    MAX_PENDING = 200
    MAX_LESSON = 1000
    MAX_EVIDENCE = 2000

    def __init__(
        self,
        *,
        consent_for_owner: Callable[[str], Any],
        memory_for_owner: Callable[[str], Any],
        queue_store: Any = None,
    ):
        if not callable(consent_for_owner) or not callable(memory_for_owner):
            raise TypeError("owner-scoped consent and memory resolvers are required.")
        if queue_store is not None and not all(
            callable(getattr(queue_store, name, None))
            for name in ("add", "pending", "claim", "resolve", "release")
        ):
            raise TypeError("queue_store does not implement the learning queue contract.")
        self._consent_for_owner = consent_for_owner
        self._memory_for_owner = memory_for_owner
        self._queue_store = queue_store
        self._pending: dict[str, LearningCandidate] = {}
        self._lock = RLock()

    @staticmethod
    def _validate_owner(owner_id: str) -> str:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128:
            raise ValueError("owner_id must be non-empty text of at most 128 characters.")
        return owner_id

    def _consent_enabled(self, owner_id: str) -> bool:
        consent = self._consent_for_owner(owner_id)
        enabled = getattr(consent, "learning_enabled", False)
        return type(enabled) is bool and enabled

    def _propose(self, owner_id: str, source: str, lesson: str, evidence: str) -> LearningCandidate:
        owner_id = self._validate_owner(owner_id)
        if source not in {"foreground_feedback", "background_task"}:
            raise ValueError("unsupported learning source.")
        if not isinstance(lesson, str) or not lesson.strip() or len(lesson) > self.MAX_LESSON:
            raise ValueError("lesson must be non-empty text up to 1000 characters.")
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > self.MAX_EVIDENCE:
            raise ValueError("evidence must be non-empty text up to 2000 characters.")
        if not self._consent_enabled(owner_id):
            raise PermissionError("learning is not enabled for this owner.")
        candidate = LearningCandidate(
            candidate_id=uuid4().hex, owner_id=owner_id, source=source,
            lesson=lesson.strip(), evidence=evidence.strip(),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        if self._queue_store is not None:
            self._queue_store.add(candidate)
        else:
            with self._lock:
                if len(self._pending) >= self.MAX_PENDING:
                    raise RuntimeError("learning review queue is full.")
                self._pending[candidate.candidate_id] = candidate
        return candidate

    def propose_foreground_feedback(self, owner_id: str, *, lesson: str, evidence: str) -> LearningCandidate:
        """Capture an explicit user-provided correction or lesson, not raw chat."""
        return self._propose(owner_id, "foreground_feedback", lesson, evidence)

    def propose_background_outcome(
        self, owner_id: str, *, lesson: str, evidence: str, verified: bool,
    ) -> LearningCandidate:
        """Capture a verified task lesson from a background worker for review."""
        if type(verified) is not bool or not verified:
            raise ValueError("background learning requires a verified task outcome.")
        return self._propose(owner_id, "background_task", lesson, evidence)

    def pending(self, owner_id: str, *, limit: int = 50) -> list[LearningCandidate]:
        owner_id = self._validate_owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        if self._queue_store is not None:
            return [LearningCandidate(**row) for row in self._queue_store.pending(owner_id, limit)]
        with self._lock:
            return [item for item in list(self._pending.values()) if item.owner_id == owner_id][:limit]

    def review(self, owner_id: str, candidate_id: str, *, approved: bool) -> bool:
        """Claim, recheck consent, then idempotently save and resolve a candidate."""
        owner_id = self._validate_owner(owner_id)
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("candidate_id must be non-empty text.")
        if type(approved) is not bool:
            raise ValueError("approved must be a boolean.")

        if self._queue_store is None:
            with self._lock:
                candidate = self._pending.get(candidate_id)
                if candidate is None or candidate.owner_id != owner_id:
                    return False
                if not approved:
                    del self._pending[candidate_id]
                    return True
                if not self._consent_enabled(owner_id):
                    raise PermissionError("learning consent is no longer enabled.")
                memory = self._memory_for_owner(owner_id)
                add = getattr(memory, "add", None)
                if not callable(add):
                    raise TypeError("owner memory backend must provide add().")
                add(candidate.lesson, category="lesson", source_id=candidate.candidate_id)
                del self._pending[candidate_id]
                return True

        if not self._queue_store.claim(owner_id, candidate_id):
            return False
        rows = self._queue_store.pending(owner_id, limit=100)
        # The claimed row is intentionally absent from pending; load it through
        # the owner-scoped review payload before committing.
        getter = getattr(self._queue_store, "get_reviewing", None)
        candidate_data = getter(owner_id, candidate_id) if callable(getter) else None
        if candidate_data is None:
            self._queue_store.release(owner_id, candidate_id)
            return False
        candidate = LearningCandidate(**candidate_data)
        try:
            if approved:
                if not self._consent_enabled(owner_id):
                    raise PermissionError("learning consent is no longer enabled.")
                memory = self._memory_for_owner(owner_id)
                add = getattr(memory, "add", None)
                if not callable(add):
                    raise TypeError("owner memory backend must provide add().")
                add(candidate.lesson, category="lesson", source_id=candidate.candidate_id)
            return self._queue_store.resolve(owner_id, candidate_id, approved=approved)
        except Exception:
            self._queue_store.release(owner_id, candidate_id)
            raise
