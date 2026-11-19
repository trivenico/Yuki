"""Authorized application service for owner-scoped observation knowledge review.

UI layers receive this service, never the SQLite store. Every operation checks
an application-supplied actor authorization callback; this layer does not grant
permissions or promote approved proposals into active memory.
"""
from __future__ import annotations

from collections.abc import Callable

from core.observation_knowledge_store import SQLiteObservationKnowledgeReview


class ObservationKnowledgeReviewService:
    """Permission-checking facade over one owner's durable review store."""

    def __init__(
        self,
        store: SQLiteObservationKnowledgeReview,
        *,
        authorize: Callable[[str, str], bool],
    ) -> None:
        if not isinstance(store, SQLiteObservationKnowledgeReview):
            raise TypeError("store must be a SQLiteObservationKnowledgeReview")
        if not callable(authorize):
            raise TypeError("authorize must be callable")
        self._store = store
        self._authorize = authorize

    def _require(self, actor_id: str, permission: str) -> str:
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200:
            raise ValueError("actor_id must be non-empty bounded text")
        actor_id = actor_id.strip()
        try:
            allowed = self._authorize(actor_id, permission)
        except Exception as exc:
            raise PermissionError("observation review authorization failed") from exc
        if allowed is not True:
            raise PermissionError("observation review operation is not authorized")
        return actor_id

    def list_pending(self, *, actor_id: str, limit: int = 100):
        self._require(actor_id, "observation:review:read")
        return self._store.list_pending(limit=limit)

    def get(self, proposal_id: str, *, actor_id: str):
        self._require(actor_id, "observation:review:read")
        return self._store.get(proposal_id)

    def list_review_events(self, proposal_id: str, *, actor_id: str, limit: int = 100):
        self._require(actor_id, "observation:review:read")
        return self._store.list_review_events(proposal_id, limit=limit)

    def approve(self, proposal_id: str, *, actor_id: str):
        reviewer_id = self._require(actor_id, "observation:review:decide")
        return self._store.approve(proposal_id, reviewer_id=reviewer_id)

    def reject(self, proposal_id: str, *, actor_id: str):
        reviewer_id = self._require(actor_id, "observation:review:decide")
        return self._store.reject(proposal_id, reviewer_id=reviewer_id)

    def delete(self, proposal_id: str, *, actor_id: str) -> bool:
        self._require(actor_id, "observation:review:delete")
        return self._store.delete(proposal_id)


__all__ = ["ObservationKnowledgeReviewService"]
