"""Local hybrid memory retrieval with an optional injected embedding function."""
import math
import re
from pathlib import Path
from companion.memory import MemoryStore


class VectorMemory:
    def __init__(self, path: str | Path = "yuki_memory.json", *, embedding_fn=None, semantic_weight=0.7):
        if embedding_fn is not None and not callable(embedding_fn):
            raise TypeError("embedding_fn must be callable")
        if isinstance(semantic_weight, bool) or not isinstance(semantic_weight, (int, float)) or not 0 <= semantic_weight <= 1:
            raise ValueError("semantic_weight must be between 0 and 1")
        self.store = MemoryStore(path)
        self.embedding_fn = embedding_fn
        self.semantic_weight = float(semantic_weight)

    @staticmethod
    def _tokens(text):
        return set(re.findall(r"[a-z0-9_]{2,}", text.lower()))

    @staticmethod
    def _vector(value):
        try:
            values = tuple(float(x) for x in value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not values or len(values) > 16384 or any(not math.isfinite(x) for x in values):
            return None
        norm = math.sqrt(sum(x*x for x in values))
        return tuple(x/norm for x in values) if norm else None

    def remember(self, text, *, category="lesson"):
        return self.store.add(text, category=category)

    def search(self, query, *, limit=5):
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("Search query must be non-empty and bounded.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20.")
        tokens = self._tokens(query)
        query_vector = None
        if self.embedding_fn is not None:
            try:
                query_vector = self._vector(self.embedding_fn(query.strip()))
            except Exception:
                pass
        ranked = []
        for item in self.store.list(limit=200):
            content_tokens = self._tokens(item["text"])
            lexical = len(tokens & content_tokens) / max(1, len(tokens | content_tokens))
            semantic = None
            if query_vector is not None:
                try:
                    vector = self._vector(self.embedding_fn(item["text"]))
                    if vector is not None and len(vector) == len(query_vector):
                        semantic = max(0.0, min(1.0, sum(a*b for a,b in zip(query_vector, vector)))
                except Exception:
                    pass
            if semantic is None:
                score, mode = lexical, "lexical"
            else:
                score = self.semantic_weight * semantic + (1-self.semantic_weight) * lexical
                mode = "hybrid" if lexical else "semantic"
            if score > 0:
                ranked.append((score, item["created_at"], {**item, "score": round(score, 4), "retrieval": mode}))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [item for _, _, item in ranked[:limit]]
