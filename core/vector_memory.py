"""Local long-term searchable memory with transparent lexical ranking.

This intentionally uses the standard library rather than claiming to be an embedding
vector database. The storage API can later be adapted to a local embedding backend.
"""
from __future__ import annotations

import re
from pathlib import Path
from companion.memory import MemoryStore


class VectorMemory:
    def __init__(self, path: str | Path = "yuki_memory.json"):
        self.store = MemoryStore(path)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9_]{2,}", text.lower())}

    def remember(self, text: str, *, category: str = "lesson") -> dict:
        return self.store.add(text, category=category)

    def search(self, query: str, *, limit: int = 5) -> list[dict]:
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("Search query must be non-empty and bounded.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20.")
        query_tokens = self._tokens(query)
        if not query_tokens:
            return []
        ranked = []
        for item in self.store.list(limit=200):
            tokens = self._tokens(item["text"])
            overlap = len(query_tokens & tokens)
            if overlap:
                score = overlap / max(1, len(query_tokens | tokens))
                ranked.append((score, item))
        ranked.sort(key=lambda pair: (pair[0], pair[1]["created_at"]), reverse=True)
        return [{"score": round(score, 4), **item} for score, item in ranked[:limit]]
