"""Unified, owner-authorized memory facade shared by Yuki application surfaces.

The facade deliberately accepts a trusted authorization callback from the
application composition root; it never invents identity or grants permissions.
"""
from __future__ import annotations

from typing import Callable, Iterable

from automation.conversation_memory import ConversationMemory, ConversationMemoryStore
from providers.ollama_embeddings import OllamaEmbeddingProvider


class YukiMemoryService:
    """Stable application API for conversation import, retrieval and deletion."""

    def __init__(self, store: ConversationMemoryStore) -> None:
        if not isinstance(store, ConversationMemoryStore):
            raise TypeError("store must be a ConversationMemoryStore.")
        self.store = store

    def import_conversation(
        self, *, actor_id: str, owner_id: str, source: str,
        records: Iterable[dict], consent: bool,
    ) -> int:
        return self.store.import_records(
            actor_id=actor_id, owner_id=owner_id, source=source,
            records=records, consent=consent,
        )

    def retrieve(
        self, *, actor_id: str, owner_id: str, query: str,
        limit: int = 10, semantic: bool = True,
    ) -> list[ConversationMemory]:
        if type(semantic) is not bool:
            raise ValueError("semantic must be a boolean.")
        search = self.store.semantic_search if semantic else self.store.search
        return search(actor_id=actor_id, owner_id=owner_id, query=query, limit=limit)

    def delete(self, *, actor_id: str, owner_id: str, memory_id: int) -> bool:
        return self.store.delete(actor_id=actor_id, owner_id=owner_id, memory_id=memory_id)

    def clear_owner(self, *, actor_id: str, owner_id: str) -> int:
        return self.store.clear_owner(actor_id=actor_id, owner_id=owner_id)

    def count(self, *, actor_id: str, owner_id: str) -> int:
        return self.store.count(actor_id=actor_id, owner_id=owner_id)


def build_memory_service(
    database_path: str, *,
    authorize: Callable[[str, str, str], bool],
    embedding_provider: Callable[[str], Iterable[float]] | None = None,
    embedding_model_id: str = "default",
) -> YukiMemoryService:
    """Build the shared memory service with an optional local embedding backend."""
    store = ConversationMemoryStore(
        database_path,
        authorize=authorize,
        embedding_fn=embedding_provider,
        embedding_model_id=embedding_model_id,
    )
    return YukiMemoryService(store)


def build_ollama_memory_service(
    database_path: str, *,
    authorize: Callable[[str, str, str], bool],
    model: str,
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 30,
) -> YukiMemoryService:
    """Compose Yuki memory with local Ollama embeddings; network calls are lazy."""
    provider = OllamaEmbeddingProvider(base_url=base_url, model=model, timeout=timeout)
    return build_memory_service(
        database_path,
        authorize=authorize,
        embedding_provider=provider,
        embedding_model_id=f"ollama:{model.strip()}",
    )
