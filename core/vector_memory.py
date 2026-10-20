"""Local long-term lexical memory; no embeddings or network calls."""

import re
from pathlib import Path
from companion.memory import MemoryStore


class VectorMemory:
    def __init__(self, path: str | Path = "yuki_memory.json"):
        self.store = MemoryStore(path)

    @staticmethod
    def _tokens(text):
        return set(re.findall(r"[a-z0-9_]{2,}", text.lower()))

    def remember(self, text, *, category="lesson"):
        return self.store.add(text, category=category)

    def search(self, query, *, limit=5):
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("Search query must be non-empty and bounded.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20.")
        tokens = self._tokens(query)
        if not tokens:
            return []
        ranked = []
        for item in self.store.list(limit=200):
            content_tokens = self._tokens(item["text"])
            overlap = len(tokens & content_tokens)
            if overlap:
                ranked.append((overlap / max(1, len(tokens | content_tokens)), item))
        ranked.sort(key=lambda row: (row[0], row[1]["created_at"]), reverse=True)
        return [{"score": round(score, 4), **item} for score, item in ranked[:limit]]
