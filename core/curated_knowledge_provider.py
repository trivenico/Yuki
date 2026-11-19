"""Live-authorized, bounded relevance retrieval for curated knowledge."""
from __future__ import annotations

import re
from collections.abc import Callable

from core.curated_knowledge_store import SQLiteCuratedKnowledge
from core.observation_review_control import ObservationKnowledgeReviewService


_TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]{1,127}", re.IGNORECASE)
_STOP_WORDS = {
    "about", "after", "also", "and", "are", "can", "for", "from", "help",
    "how", "into", "its", "me", "my", "the", "this", "that", "with", "you",
}


class CuratedKnowledgeProvider:
    """Owner-scoped retrieval callable for ConversationApplicationRuntime.

    Authorization is performed through the review service on every invocation.
    No cache is used, so permission revocation and catalog deactivation take
    effect on the next request.
    """

    def __init__(
        self,
        review_service: ObservationKnowledgeReviewService,
        catalog: SQLiteCuratedKnowledge,
        *,
        actor_id_provider: Callable[[str], str],
        max_candidates: int = 100,
        max_results: int = 5,
    ) -> None:
        if not isinstance(review_service, ObservationKnowledgeReviewService):
            raise TypeError("review_service must be an ObservationKnowledgeReviewService")
        if not isinstance(catalog, SQLiteCuratedKnowledge):
            raise TypeError("catalog must be a SQLiteCuratedKnowledge")
        if not callable(actor_id_provider):
            raise TypeError("actor_id_provider must be callable")
        if type(max_candidates) is not int or not 1 <= max_candidates <= 100:
            raise ValueError("max_candidates must be between 1 and 100")
        if type(max_results) is not int or not 1 <= max_results <= 5:
            raise ValueError("max_results must be between 1 and 5")
        self._review = review_service
        self._catalog = catalog
        self._actor_id_provider = actor_id_provider
        self._max_candidates = max_candidates
        self._max_results = max_results

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {
            token.lower() for token in _TOKEN.findall(text)
            if token.lower() not in _STOP_WORDS
        }

    def __call__(self, owner_id: str, query: str):
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 256:
            raise ValueError("owner_id must be non-empty bounded text")
        if owner_id.strip() != self._catalog.owner_id:
            raise PermissionError("requested owner does not match the knowledge catalog")
        if not isinstance(query, str) or not query.strip() or len(query) > 20_000:
            raise ValueError("query must be non-empty text of at most 20000 characters")

        actor_id = self._actor_id_provider(owner_id.strip())
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise PermissionError("no authenticated actor is available for knowledge retrieval")

        # This call checks the live actor permission and catalog owner on every
        # request. Do not move it behind a cache lookup.
        entries = self._review.list_curated(
            actor_id=actor_id, catalog=self._catalog, limit=self._max_candidates
        )
        query_tokens = self._tokens(query)
        if not query_tokens:
            return ()

        ranked = []
        for entry in entries:
            searchable = self._tokens(
                f"{entry['kind']} {entry['value']} {entry['summary']}"
            )
            overlap = query_tokens.intersection(searchable)
            if not overlap:
                continue
            # Exact metadata value mention wins; otherwise prefer broader token
            # overlap. Stable entry ID is the final tie-breaker.
            normalized_query = query.casefold()
            value = str(entry["value"]).casefold()
            exact_value = bool(value and value in normalized_query)
            score = (int(exact_value), len(overlap), len(overlap) / len(query_tokens))
            ranked.append((score, str(entry["entry_id"]), entry))

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return tuple(item[2] for item in ranked[: self._max_results])


__all__ = ["CuratedKnowledgeProvider"]
