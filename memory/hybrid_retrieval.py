"""Pluggable hybrid lexical/embedding retrieval over ACL-filtered memory.

The embedding provider is injected by the application. This module performs no
network calls and never allows vector search to widen the vault's ACL scope.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Callable, Iterable, Sequence

from memory.collective_vault import CollectiveMemoryVault, MemoryRecord

_TOKEN = re.compile(r"[a-z0-9_]{2,}")


class RetrievalError(ValueError):
    """Invalid embedding or retrieval configuration."""


@dataclass(frozen=True)
class HybridMemoryResult:
    record: MemoryRecord
    score: float
    lexical_score: float
    semantic_score: float | None


class HybridMemoryRetriever:
    """Combine lexical overlap and cosine similarity from an injected provider."""

    def __init__(
        self,
        vault: CollectiveMemoryVault,
        *,
        embed: Callable[[str], Sequence[float]] | None = None,
        lexical_weight: float = 0.35,
        candidate_limit: int = 500,
        minimum_similarity: float = -1.0,
    ):
        if not isinstance(vault, CollectiveMemoryVault):
            raise TypeError("vault must be a CollectiveMemoryVault.")
        if embed is not None and not callable(embed):
            raise TypeError("embed must be callable or None.")
        if (isinstance(lexical_weight, bool) or not isinstance(lexical_weight, (int, float))
                or not math.isfinite(lexical_weight) or not 0 <= lexical_weight <= 1):
            raise RetrievalError("lexical_weight must be finite and between 0 and 1.")
        if type(candidate_limit) is not int or not 1 <= candidate_limit <= 5000:
            raise RetrievalError("candidate_limit must be between 1 and 5000.")
        if (isinstance(minimum_similarity, bool)
                or not isinstance(minimum_similarity, (int, float))
                or not math.isfinite(minimum_similarity)
                or not -1 <= minimum_similarity <= 1):
            raise RetrievalError("minimum_similarity must be between -1 and 1.")
        self.vault = vault
        self.embed = embed
        self.lexical_weight = float(lexical_weight)
        self.candidate_limit = candidate_limit
        self.minimum_similarity = float(minimum_similarity)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(_TOKEN.findall(text.casefold()))

    @staticmethod
    def _vector(value: Sequence[float]) -> tuple[float, ...]:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise RetrievalError("embedding provider must return a numeric sequence.")
        if not 1 <= len(value) <= 8192:
            raise RetrievalError("embedding dimension must be between 1 and 8192.")
        vector = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
                raise RetrievalError("embedding values must be finite numbers.")
            vector.append(float(item))
        if not any(vector):
            raise RetrievalError("zero vectors cannot be compared.")
        return tuple(vector)

    @staticmethod
    def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
        if len(left) != len(right):
            raise RetrievalError("embedding dimensions do not match.")
        dot = sum(a * b for a, b in zip(left, right))
        norm_left = math.sqrt(sum(a * a for a in left))
        norm_right = math.sqrt(sum(b * b for b in right))
        return max(-1.0, min(1.0, dot / (norm_left * norm_right)))

    @staticmethod
    def _lexical(query: set[str], content: str) -> float:
        tokens = HybridMemoryRetriever._tokens(content)
        if not query or not tokens:
            return 0.0
        return len(query & tokens) / len(query | tokens)

    def search(
        self, *, actor_id: str, query: str,
        scopes: Iterable[tuple[str, str]], limit: int = 10,
    ) -> tuple[HybridMemoryResult, ...]:
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise RetrievalError("query must be non-empty bounded text.")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise RetrievalError("limit must be between 1 and 50.")
        candidates = self.vault.scoped_candidates(
            actor_id=actor_id, scopes=scopes, limit=self.candidate_limit
        )
        if not candidates:
            return ()
        query_tokens = self._tokens(query)
        if self.embed is None:
            results = [
                HybridMemoryResult(record, score, score, None)
                for record in candidates
                if (score := self._lexical(query_tokens, record.content)) > 0
            ]
        else:
            try:
                query_vector = self._vector(self.embed(query))
                results = []
                for record in candidates:
                    semantic = self._cosine(
                        query_vector, self._vector(self.embed(record.content))
                    )
                    if semantic < self.minimum_similarity:
                        continue
                    lexical = self._lexical(query_tokens, record.content)
                    semantic_normalized = (semantic + 1.0) / 2.0
                    score = (
                        self.lexical_weight * lexical
                        + (1.0 - self.lexical_weight) * semantic_normalized
                    )
                    results.append(HybridMemoryResult(
                        record, round(score, 6), round(lexical, 6), round(semantic, 6)
                    ))
            except RetrievalError:
                raise
            except Exception as exc:
                raise RetrievalError("embedding provider failed; no partial results returned.") from exc
        results.sort(key=lambda item: (item.score, item.record.updated_at), reverse=True)
        return tuple(results[:limit])
