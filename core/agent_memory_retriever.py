"""Evidence-aware retrieval over permission-filtered Yuki memories.

The store remains the authorization boundary. Embeddings only rank records
already returned by AgentMemoryStore; they can never widen visibility.
Retrieved text remains untrusted reference data.
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
        max_candidates: int = 500,
    ):
        if not isinstance(memory_store, AgentMemoryStore):
            raise TypeError("memory_store_must_be_AgentMemoryStore")
        if not callable(embed):
            raise TypeError("embedding_callable_required")
        if type(max_candidates) is not int or not 1 <= max_candidates <= 2000:
            raise ValueError("max_candidates_must_be_1_to_2000")
        if embedding_index is not None:
            if not isinstance(embedding_index, SQLiteEmbeddingIndex):
                raise TypeError("embedding_index_must_be_SQLiteEmbeddingIndex")
            if embedding_index.database_path != memory_store.database_path:
                raise ValueError("retrieval_stores_must_share_database")
        self.memory_store = memory_store
        self.embed = embed
        self.embedding_index = embedding_index
        self.max_candidates = max_candidates

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
        return 0.5 ** (age_days / 30.0)

    def _authorized_candidates(
        self, *, actor_id: str, agent_id: str,
        include_shared: bool, verified_only: bool,
    ) -> list[dict]:
        """Scan a bounded keyset window; each page rechecks store authorization.

        The budget counts raw rows returned by the store, including records
        excluded by verified_only. Separate page reads are best-effort rather
        than one SQLite snapshot; permission failures abort the retrieval.
        """
        candidates: list[dict] = []
        cursor = None
        scanned = 0
        while scanned < self.max_candidates:
            page_size = min(100, self.max_candidates - scanned)
            page = self.memory_store.list_memories_page(
                actor_id=actor_id, agent_id=agent_id, limit=page_size,
                include_shared=include_shared, before=cursor,
            )
            if not page:
                break
            scanned += len(page)
            if verified_only:
                candidates.extend(item for item in page if item["verified"])
            else:
                candidates.extend(page)
            last = page[-1]
            cursor = {"created_at": last["created_at"], "memory_id": last["memory_id"]}
            if len(page) < page_size:
                break
        return candidates

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

        memories = self._authorized_candidates(
            actor_id=actor_id, agent_id=agent_id,
            include_shared=include_shared, verified_only=verified_only,
        )
        if not memories:
            return []

        query_vector = self._vector(self.embed(query.strip()))
        vectors: dict[str, list[float]] = {}

        # The embedding index intentionally caps each operation at 100.
        # Process bounded pages in matching batches instead of passing the
        # complete candidate window to one cache call.
        for offset in range(0, len(memories), 100):
            batch = memories[offset:offset + 100]
            cached = {}
            if self.embedding_index is not None:
                cached = self.embedding_index.get_many(actor_id=actor_id, memories=batch)
            missing = [item for item in batch if item["memory_id"] not in cached]
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
            vectors.update(cached)
            vectors.update({
                item["memory_id"]: vector
                for item, vector in zip(missing, missing_vectors)
            })

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
