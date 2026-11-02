"""Bounded, scope-isolated lexical memory retrieval.

This is a transparent offline baseline, not embedding-based semantic search.
It ranks only memories already fetched under an explicit actor and scope.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re

from core.memory import Memory, MemoryStore

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]{1,40}", re.I)
_STOP = frozenset({"a","an","and","are","as","at","be","by","for","from","how",
                   "i","in","is","it","of","on","or","our","the","this","to",
                   "we","what","when","where","with","you","your"})


@dataclass(frozen=True)
class RetrievedMemory:
    memory: Memory
    score: float
    matched_terms: tuple[str, ...]


def _tokens(value: str) -> list[str]:
    return [term.casefold() for term in _TOKEN.findall(value) if term.casefold() not in _STOP]


class MemoryRetriever:
    """Rank memories with BM25-style lexical relevance and strict scope isolation."""

    def __init__(self, store: MemoryStore):
        self.store = store

    def retrieve(self, *, actor_id: str, scope_id: str, query: str,
                 category: str | None = None, limit: int = 10) -> list[RetrievedMemory]:
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("query must be non-empty and at most 500 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        terms = list(dict.fromkeys(_tokens(query)))
        if not terms:
            return []
        # Scope is enforced in SQL before ranking. The cap bounds CPU and memory.
        memories = self.store.list(actor_id=actor_id, scope_id=scope_id,
                                   category=category, limit=200)
        if not memories:
            return []
        docs = [_tokens(item.content) for item in memories]
        doc_freq = Counter(term for doc in docs for term in set(doc))
        avg_len = sum(map(len, docs)) / max(1, len(docs))
        k1, b = 1.5, 0.75
        ranked: list[RetrievedMemory] = []
        for memory, doc in zip(memories, docs):
            frequencies = Counter(doc)
            score = 0.0
            matched = []
            for term in terms:
                tf = frequencies.get(term, 0)
                if not tf:
                    continue
                matched.append(term)
                df = doc_freq[term]
                idf = math.log(1 + (len(docs) - df + 0.5) / (df + 0.5))
                denominator = tf + k1 * (1 - b + b * len(doc) / max(1.0, avg_len))
                score += idf * (tf * (k1 + 1)) / denominator
            if score > 0:
                ranked.append(RetrievedMemory(memory, round(score, 6), tuple(matched)))
        ranked.sort(key=lambda item: (-item.score, item.memory.memory_id))
        return ranked[:limit]
