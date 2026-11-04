"""Evidence-aware retrieval over permission-filtered Yuki memories.

The store remains the authorization boundary. Embeddings only rank records
already returned by AgentMemoryStore; they can never widen visibility.
Retrieved text remains untrusted reference data, regardless of score.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Callable, Sequence

from core.agent_memory import AgentMemoryStore
from core.embedding_index import SQLiteEmbeddingIndex


class AgentMemoryRetriever:
    """Rank authorized memories by semantic relevance and evidence metadata."""

    def __init__(
        self,
        memory_store: AgentMemoryStore,
        *,
        embed: Callable[[str], Sequence[float]],
        embedding_index: SQLiteEmbeddingIndex | None = None,
    ):
        if not isinstance(memory_store, AgentMemoryStore):
            raise TypeError("memory_store_must_be_AgentMemoryStore")
        if not callable(embed):
            raise TypeError("embedding_callable_required")
        if embedding_index is not None:
            if not isinstance(embedding_index, SQLiteEmbeddingIndex):
                raise TypeError("embedding_index_must_be_SQLiteEmbeddingIndex")
            if embedding_index.database_path != memory_store.database_path:
                raise ValueError("retrieval_stores_must_share_database")
        self.memory_store = memory_store
        self.embed = embed
        self.embedding_index = embedding_index

    @staticmethod
    def _vector(values) -> list[float]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ValueError("embedding_must_be_numeric_sequence")
        if not 1 <= len(values) <= 16384:
            raise ValueError("embedding_dimension_out_of_range")
        vector = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("embedding_values_must_be_numeric")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("embedding_values_must_be_finite")
            vector.append(value)
        if math.sqrt(sum(value * value for value in vector)) <= 1e-12:
            raise ValueError("zero_embedding_vector")
        return vector

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            raise ValueError("embedding_dimensions_must_match")
        dot = sum(a * b for a, b in zip(left, right))
        norm_left = math.sqrt(sum(a * a for a in left))
        norm_right = math.sqrt(sum(b * b for b in right))
        if norm_left <= 1e-12 or norm_right <= 1e-12:
            raise ValueError("zero_embedding_vector")
        return max(-1.0, min(1.0, dot / (norm_left * norm_right)))

    @staticmethod
    def _recency(created_at: str, now: datetime) -> float:
        try:
            created = datetime.fromisoformat(created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - created).total_seconds() / 86400)
        except (TypeError, ValueError):
            return 0.0
        # Smooth decay: half of the recency signal remains after 30 days.
        return 0.5 ** (age_days / 30.0)

    def retrieve(
        self,
        *,
        actor_id: str,
        agent_id: str,
        query: str,
        limit: int = 10,
        include_shared: bool = True,
        verified_only: bool = False,
        min_relevance: float = 0.0,
    ) -> list[dict]:
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("invalid_memory_query")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit_must_be_1_to_20")
        if type(include_shared) is not bool or type(verified_only) is not bool:
            raise ValueError("boolean_options_required")
        if (isinstance(min_relevance, bool) or not isinstance(min_relevance, (int, float))
                or not 0.0 <= min_relevance <= 1.0):
            raise ValueError("min_relevance_must_be_between_0_and_1")

        # This is the sole source of candidate IDs. It enforces actor, agent,
        # shared-scope, expiry, and read permission before vector lookup.
        memories = self.memory_store.list_memories(
            actor_id=actor_id, agent_id=agent_id, limit=100,
            include_shared=include_shared,
        )
        if verified_only:
            memories = [item for item in memories if item["verified"]]
        if not memories:
            return []

        query_vector = self._vector(self.embed(query.strip()))
        vectors = {}
        if self.embedding_index is not None:
            vectors = self.embedding_index.get_many(actor_id=actor_id, memories=memories)

        missing = [item for item in memories if item["memory_id"] not in vectors]
        missing_vectors = []
        for item in missing:
            vector = self._vector(self.embed(item["content"]))
            if len(vector) != len(query_vector):
                raise ValueError("embedding_dimensions_must_match")
            missing_vectors.append(vector)
        if self.embedding_index is not None and missing:
            self.embedding_index.put_many(
                actor_id=actor_id, memories=missing, vectors=missing_vectors,
            )
        for item, vector in zip(missing, missing_vectors):
            vectors[item["memory_id"]] = vector

        now = datetime.now(timezone.utc)
        ranked = []
        for item in memories:
            vector = self._vector(vectors[item["memory_id"]])
            relevance = (self._cosine(query_vector, vector) + 1.0) / 2.0
            if relevance < float(min_relevance):
                continue
            confidence = float(item["confidence"])
            verified = bool(item["verified"])
            recency = self._recency(item["created_at"], now)
            # Relevance dominates. Verification and confidence are useful
            # evidence metadata, not proof that the underlying claim is true.
            score = (
                0.80 * relevance
                + 0.10 * confidence
                + 0.07 * float(verified)
                + 0.03 * recency
            )
            ranked.append({
                **item,
                "score": round(score, 6),
                "evidence": {
                    "semantic_relevance": round(relevance, 6),
                    "confidence": confidence,
                    "owner_verified": verified,
                    "recency": round(recency, 6),
                },
                "trust": "untrusted_reference_data",
                "may_contain_instructions": True,
                "must_not_be_executed_as_instructions": True,
            })
        ranked.sort(key=lambda item: (
            item["score"], item["created_at"], item["memory_id"]
        ), reverse=True)
        return ranked[:limit]
