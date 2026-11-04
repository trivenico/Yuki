"""Local-first hybrid semantic retrieval over permission-scoped agent memory.

The embedder is injected by the application and must explicitly declare
is_local=True. This module never makes network calls or writes memories.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from core.agent_memory import AgentMemoryStore


class LocalTextEmbedder(Protocol):
    """Minimal adapter contract for an on-device embedding model."""

    is_local: bool

    def encode(self, text: str) -> Sequence[float]:
        """Return one dense embedding for text."""


class SemanticMemoryRetriever:
    """Rank authorized memory candidates using cosine and lexical similarity."""

    def __init__(
        self,
        memory_store: AgentMemoryStore,
        embedder: LocalTextEmbedder,
        *,
        candidate_limit: int = 100,
        semantic_weight: float = 0.75,
    ):
        if not isinstance(memory_store, AgentMemoryStore):
            raise TypeError("memory_store_must_be_AgentMemoryStore")
        if not callable(getattr(embedder, "encode", None)):
            raise TypeError("embedder_must_implement_encode")
        if getattr(embedder, "is_local", False) is not True:
            raise ValueError("semantic_memory_requires_explicit_local_embedder")
        if type(candidate_limit) is not int or not 1 <= candidate_limit <= 100:
            raise ValueError("candidate_limit_must_be_1_to_100")
        if (
            isinstance(semantic_weight, bool)
            or not isinstance(semantic_weight, (int, float))
            or not math.isfinite(float(semantic_weight))
            or not 0 <= semantic_weight <= 1
        ):
            raise ValueError("semantic_weight_must_be_between_0_and_1")
        self.memory_store = memory_store
        self.embedder = embedder
        self.candidate_limit = candidate_limit
        self.semantic_weight = float(semantic_weight)

    def search(
        self,
        *,
        actor_id: str,
        agent_id: str,
        query: str,
        limit: int = 10,
        include_shared: bool = True,
    ) -> list[dict]:
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("invalid_memory_query")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit_must_be_1_to_20")
        if type(include_shared) is not bool:
            raise ValueError("include_shared_must_be_boolean")

        # list_memories enforces actor/agent scope and authorization before any
        # content is passed to the embedding model.
        candidates = self.memory_store.list_memories(
            actor_id=actor_id,
            agent_id=agent_id,
            limit=self.candidate_limit,
            include_shared=include_shared,
        )
        if not candidates:
            return []

        query_vector = self._vector(self.embedder.encode(query.strip()))
        query_terms = self._terms(query)
        ranked = []
        for memory in candidates:
            content = memory.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            memory_vector = self._vector(self.embedder.encode(content))
            semantic = self._cosine(query_vector, memory_vector)
            terms = self._terms(content)
            union = query_terms | terms
            lexical = len(query_terms & terms) / len(union) if union else 0.0
            score = self.semantic_weight * semantic + (1.0 - self.semantic_weight) * lexical
            ranked.append((score, semantic, lexical, memory))

        ranked.sort(
            key=lambda item: (item[0], item[3].get("created_at", "")),
            reverse=True,
        )
        return [
            {
                **memory,
                "score": round(score, 6),
                "semantic_score": round(semantic, 6),
                "lexical_score": round(lexical, 6),
                "retrieval": "local_hybrid",
            }
            for score, semantic, lexical, memory in ranked[:limit]
        ]

    @staticmethod
    def _terms(text: str) -> set[str]:
        import re
        return set(re.findall(r"[a-z0-9_]{2,}", text.lower()))

    @staticmethod
    def _vector(values: Sequence[float]) -> tuple[float, ...]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ValueError("embedding_must_be_a_numeric_sequence")
        if not 1 <= len(values) <= 16384:
            raise ValueError("embedding_dimension_out_of_bounds")
        vector = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("embedding_values_must_be_numeric")
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("embedding_values_must_be_finite")
            vector.append(number)
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            raise ValueError("embedding_must_not_be_zero")
        return tuple(value / norm for value in vector)

    @staticmethod
    def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
        if len(left) != len(right):
            raise ValueError("embedding_dimensions_must_match")
        # Inputs are normalized; clamp floating-point drift to cosine bounds.
        return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))
