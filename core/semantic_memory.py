"""Local-first hybrid retrieval over permission-scoped agent memory."""
from __future__ import annotations
import math
from collections.abc import Sequence
from typing import Protocol
from core.agent_memory import AgentMemoryStore
from core.embedding_index import SQLiteEmbeddingIndex


class LocalTextEmbedder(Protocol):
    is_local: bool
    def encode(self, text: str) -> Sequence[float]: ...


class SemanticMemoryRetriever:
    def __init__(self, memory_store: AgentMemoryStore, embedder: LocalTextEmbedder,
                 *, candidate_limit: int = 100, semantic_weight: float = 0.75,
                 vector_index: SQLiteEmbeddingIndex | None = None):
        if not isinstance(memory_store, AgentMemoryStore):
            raise TypeError("memory_store_must_be_AgentMemoryStore")
        if not callable(getattr(embedder, "encode", None)):
            raise TypeError("embedder_must_implement_encode")
        if getattr(embedder, "is_local", False) is not True:
            raise ValueError("semantic_memory_requires_explicit_local_embedder")
        if type(candidate_limit) is not int or not 1 <= candidate_limit <= 100:
            raise ValueError("candidate_limit_must_be_1_to_100")
        if (isinstance(semantic_weight, bool) or not isinstance(semantic_weight, (int, float))
                or not math.isfinite(float(semantic_weight)) or not 0 <= semantic_weight <= 1):
            raise ValueError("semantic_weight_must_be_between_0_and_1")
        if vector_index is not None and not isinstance(vector_index, SQLiteEmbeddingIndex):
            raise TypeError("vector_index_must_be_SQLiteEmbeddingIndex")
        if vector_index is not None and vector_index.database_path != memory_store.database_path:
            raise ValueError("vector_index_must_share_memory_database")
        self.memory_store, self.embedder = memory_store, embedder
        self.candidate_limit, self.semantic_weight = candidate_limit, float(semantic_weight)
        self.vector_index = vector_index

    def search(self, *, actor_id: str, agent_id: str, query: str,
               limit: int = 10, include_shared: bool = True) -> list[dict]:
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("invalid_memory_query")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit_must_be_1_to_20")
        if type(include_shared) is not bool:
            raise ValueError("include_shared_must_be_boolean")
        candidates = self.memory_store.list_memories(
            actor_id=actor_id, agent_id=agent_id, limit=self.candidate_limit,
            include_shared=include_shared,
        )
        if not candidates:
            return []
        eligible = [m for m in candidates if isinstance(m.get("content"), str) and m["content"].strip()]
        if not eligible:
            return []
        qv = self._vector(self.embedder.encode(query.strip()))
        cached = self.vector_index.get_many(actor_id=actor_id, memories=eligible) if self.vector_index else {}
        missing = [m for m in eligible if m["memory_id"] not in cached]
        if missing:
            batch = getattr(self.embedder, "encode_many", None)
            if callable(batch):
                raw = batch([m["content"] for m in missing])
                if not isinstance(raw, (list, tuple)) or len(raw) != len(missing):
                    raise ValueError("embedder_returned_invalid_batch")
                fresh = [self._vector(v) for v in raw]
            else:
                fresh = [self._vector(self.embedder.encode(m["content"])) for m in missing]
            cached.update({m["memory_id"]: list(v) for m, v in zip(missing, fresh)})
            if self.vector_index:
                self.vector_index.put_many(actor_id=actor_id, memories=missing, vectors=fresh)
        qterms = self._terms(query)
        ranked = []
        for memory in eligible:
            mv = self._vector(cached[memory["memory_id"]])
            semantic = self._cosine(qv, mv)
            terms = self._terms(memory["content"])
            union = qterms | terms
            lexical = len(qterms & terms) / len(union) if union else 0.0
            score = self.semantic_weight * semantic + (1-self.semantic_weight) * lexical
            ranked.append((score, semantic, lexical, memory))
        ranked.sort(key=lambda x: (x[0], x[3].get("created_at", "")), reverse=True)
        return [{**m, "score": round(s, 6), "semantic_score": round(v, 6),
                 "lexical_score": round(l, 6), "retrieval": "local_hybrid",
                 "embedding_cached": m["memory_id"] in (cached.keys() if self.vector_index else ())}
                for s, v, l, m in ranked[:limit]]

    @staticmethod
    def _terms(text: str) -> set[str]:
        import re
        return set(re.findall(r"[a-z0-9_]{2,}", text.lower()))

    @staticmethod
    def _vector(values: Sequence[float]) -> tuple[float, ...]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not 1 <= len(values) <= 16384:
            raise ValueError("embedding_must_be_a_bounded_numeric_sequence")
        vector = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("embedding_values_must_be_finite_numeric_values")
            try:
                normalized = float(value)
            except (OverflowError, ValueError) as exc:
                raise ValueError("embedding_values_must_be_finite_numeric_values") from exc
            if not math.isfinite(normalized) or abs(normalized) > SQLiteEmbeddingIndex.MAX_ABS_COMPONENT:
                raise ValueError("embedding_values_out_of_range")
            vector.append(normalized)
        norm = math.sqrt(sum(v*v for v in vector))
        if norm <= 1e-12:
            raise ValueError("embedding_must_not_be_zero")
        return tuple(v/norm for v in vector)

    @staticmethod
    def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
        if len(left) != len(right):
            raise ValueError("embedding_dimensions_must_match")
        return max(-1.0, min(1.0, sum(a*b for a, b in zip(left, right))))
