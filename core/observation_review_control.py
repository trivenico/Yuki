"""Authorized application service for owner-scoped observation knowledge review.

UI layers receive this service, never the SQLite store. Every operation checks
an application-supplied actor authorization callback. Promotion, retrieval,
and deactivation each require separate permissions.
"""
from __future__ import annotations

from collections.abc import Callable

from core.observation_knowledge_store import SQLiteObservationKnowledgeReview
from core.curated_knowledge_store import SQLiteCuratedKnowledge


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

    def can_review(self, *, actor_id: str, decisions: bool = False) -> bool:
        """Check whether this actor may inspect proposals and optionally decide them."""
        if not isinstance(decisions, bool):
            return False
        try:
            self._require(actor_id, "observation:review:read")
            if decisions:
                self._require(actor_id, "observation:review:decide")
            return True
        except (TypeError, ValueError, PermissionError):
            return False

    def list_pending(self, *, actor_id: str, limit: int = 100):
        self._require(actor_id, "observation:review:read")
        return self._store.list_pending(limit=limit)

    def list_recent(self, *, actor_id: str, limit: int = 100):
        self._require(actor_id, "observation:review:read")
        return self._store.list_recent(limit=limit)

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

    def promote_approved(self, proposal_id: str, *, actor_id: str,
                         catalog: SQLiteCuratedKnowledge):
        """Promote one approved proposal; requires a separate promotion grant."""
        if not isinstance(catalog, SQLiteCuratedKnowledge):
            raise TypeError("catalog must be a SQLiteCuratedKnowledge")
        promoter_id = self._require(actor_id, "memory:knowledge:promote")
        if catalog.owner_id != self._store.owner_id:
            raise PermissionError("knowledge catalog owner does not match the review owner")
        proposal = self._store.get(proposal_id)
        if proposal is None or proposal.status != "approved":
            raise PermissionError("only an existing approved proposal can be promoted")
        events = self._store.list_review_events(proposal_id, limit=100)
        approvals = [event for event in events if event["decision"] == "approved"]
        if not approvals:
            raise PermissionError("approved proposal has no review provenance")
        return catalog.promote(
            proposal, approved_by=approvals[-1]["reviewer_id"],
            promoted_by=promoter_id,
        )


    def retrieve_curated(self, kind: str, value: str, *, actor_id: str,
                         catalog: SQLiteCuratedKnowledge):
        """Return an exact active match with provenance; never executes stored text."""
        if not isinstance(catalog, SQLiteCuratedKnowledge):
            raise TypeError("catalog must be a SQLiteCuratedKnowledge")
        self._require(actor_id, "memory:knowledge:read")
        if catalog.owner_id != self._store.owner_id:
            raise PermissionError("knowledge catalog owner does not match the review owner")
        return catalog.find_active(kind, value)

    def list_curated(self, *, actor_id: str, catalog: SQLiteCuratedKnowledge,
                     limit: int = 100):
        if not isinstance(catalog, SQLiteCuratedKnowledge):
            raise TypeError("catalog must be a SQLiteCuratedKnowledge")
        self._require(actor_id, "memory:knowledge:read")
        if catalog.owner_id != self._store.owner_id:
            raise PermissionError("knowledge catalog owner does not match the review owner")
        return catalog.list_active(limit=limit)

    def deactivate_curated(self, entry_id: str, *, actor_id: str,
                           catalog: SQLiteCuratedKnowledge) -> bool:
        if not isinstance(catalog, SQLiteCuratedKnowledge):
            raise TypeError("catalog must be a SQLiteCuratedKnowledge")
        actor = self._require(actor_id, "memory:knowledge:deactivate")
        if catalog.owner_id != self._store.owner_id:
            raise PermissionError("knowledge catalog owner does not match the review owner")
        return catalog.deactivate(entry_id, actor_id=actor)

    def list_curated_events(self, *, actor_id: str, catalog: SQLiteCuratedKnowledge,
                            limit: int = 100):
        if not isinstance(catalog, SQLiteCuratedKnowledge):
            raise TypeError("catalog must be a SQLiteCuratedKnowledge")
        self._require(actor_id, "memory:knowledge:read")
        if catalog.owner_id != self._store.owner_id:
            raise PermissionError("knowledge catalog owner does not match the review owner")
        return catalog.list_events(limit=limit)

    def delete(self, proposal_id: str, *, actor_id: str) -> bool:
        self._require(actor_id, "observation:review:delete")
        return self._store.delete(proposal_id)


__all__ = ["ObservationKnowledgeReviewService"]
