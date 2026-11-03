"""Local lexical memory retrieval; intentionally offline and embedding-free.

The historical class name is retained for compatibility. Retrieval is lexical,
not vector/embedding similarity. Memory writes remain explicit and opt-in.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from companion.memory import MemoryStore


_TOKEN = re.compile(r"[^\W_]{2,}", re.UNICODE)
_CATEGORIES = frozenset({"note", "preference", "lesson"})


class VectorMemory:
    """Small local memory index with deterministic BM25-style lexical ranking."""

    def __init__(self, path: str | Path = "yuki_memory.json"):
        self.store = MemoryStore(path)

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return _TOKEN.findall(text.casefold())

    def remember(self, text: str, *, category: str = "lesson") -> dict[str, Any]:
        """Persist one explicitly supplied memory; never called implicitly."""
        return self.store.add(text, category=category)

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list(limit=limit)

    def forget(self, memory_id: str) -> bool:
        """Delete one memory by its opaque ID."""
        return self.store.delete(memory_id)

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("Search query must be non-empty and bounded.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20.")
        if category is not None and category not in _CATEGORIES:
            raise ValueError("category must be note, preference, lesson, or None.")

        query_terms = self._tokens(query)
        if not query_terms:
            return []

        entries = self.store.list(limit=200)
        if category is not None:
            entries = [entry for entry in entries if entry.get("category") == category]
        documents = [(entry, self._tokens(entry["text"])) for entry in entries]
        documents = [(entry, terms) for entry, terms in documents if terms]
        if not documents:
            return []

        # BM25-style term weighting: rare terms contribute more than common ones.
        doc_count = len(documents)
        avg_length = sum(len(terms) for _, terms in documents) / doc_count
        document_frequency: dict[str, int] = {}
        for _, terms in documents:
            for term in set(terms):
                document_frequency[term] = document_frequency.get(term, 0) + 1

        query_frequency: dict[str, int] = {}
        for term in query_terms:
            query_frequency[term] = query_frequency.get(term, 0) + 1

        k1, b = 1.2, 0.75
        ranked = []
        for entry, terms in documents:
            term_frequency: dict[str, int] = {}
            for term in terms:
                term_frequency[term] = term_frequency.get(term, 0) + 1
            score = 0.0
            for term, qf in query_frequency.items():
                tf = term_frequency.get(term, 0)
                if not tf:
                    continue
                df = document_frequency.get(term, 0)
                inverse_frequency = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
                denominator = tf + k1 * (
                    1 - b + b * len(terms) / max(avg_length, 1.0)
                )
                score += inverse_frequency * (tf * (k1 + 1) / denominator) * (1 + math.log(qf))
            if score > 0:
                ranked.append((score, entry))

        ranked.sort(
            key=lambda item: (item[0], item[1]["created_at"], item[1]["id"]),
            reverse=True,
        )
        return [
            {"score": round(score, 6), **entry}
            for score, entry in ranked[:limit]
        ]
