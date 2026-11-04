"""Resumable, bounded backfill for the local semantic memory vector cache."""
from __future__ import annotations

from core.agent_memory import AgentMemoryStore
from core.embedding_index import SQLiteEmbeddingIndex


class MemoryEmbeddingBackfill:
    """Populate missing vectors from authorized source-memory pages.

    Checkpoints are scoped by actor, agent, and embedding model. Each page is
    idempotent: vectors are committed before its cursor, so interruption can
    only cause safe reprocessing, never skipped source records.
    """

    def __init__(self, memory_store: AgentMemoryStore, embedder, index: SQLiteEmbeddingIndex):
        if not isinstance(memory_store, AgentMemoryStore):
            raise TypeError("memory_store_must_be_AgentMemoryStore")
        if not isinstance(index, SQLiteEmbeddingIndex):
            raise TypeError("index_must_be_SQLiteEmbeddingIndex")
        if not callable(getattr(embedder, "encode", None)) or getattr(embedder, "is_local", False) is not True:
            raise ValueError("backfill_requires_local_embedder")
        if index.database_path != memory_store.database_path:
            raise ValueError("index_and_memory_store_must_share_database")
        self.memory_store, self.embedder, self.index = memory_store, embedder, index

    def run(self, *, actor_id: str, agent_id: str, page_size: int = 50,
            max_pages: int = 1, include_shared: bool = True,
            restart: bool = False) -> dict:
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("page_size_must_be_1_to_100")
        if type(max_pages) is not int or not 1 <= max_pages <= 100:
            raise ValueError("max_pages_must_be_1_to_100")
        if type(include_shared) is not bool or type(restart) is not bool:
            raise ValueError("boolean_options_required")
        checkpoint = self.index.get_backfill_checkpoint(actor_id=actor_id, agent_id=agent_id)
        if restart:
            checkpoint = None
            self.index.save_backfill_checkpoint(
                actor_id=actor_id, agent_id=agent_id, cursor=None, completed=False
            )
        elif checkpoint and checkpoint["completed"]:
            return {"pages_processed": 0, "memories_indexed": 0,
                    "completed": True, "checkpoint": checkpoint["cursor"],
                    "model_id": self.index.model_id}
        cursor = checkpoint["cursor"] if checkpoint else None
        pages = indexed = 0
        completed = False
        while pages < max_pages:
            memories = self.memory_store.list_memories_page(
                actor_id=actor_id, agent_id=agent_id, limit=page_size,
                include_shared=include_shared, before=cursor,
            )
            if not memories:
                self.index.save_backfill_checkpoint(
                    actor_id=actor_id, agent_id=agent_id, cursor=cursor, completed=True
                )
                completed = True
                break
            cached = self.index.get_many(actor_id=actor_id, memories=memories)
            missing = [m for m in memories if m["memory_id"] not in cached]
            vectors = []
            # Keep text payload below the local adapter's 64k batch bound.
            for offset in range(0, len(missing), 8):
                chunk = missing[offset:offset + 8]
                batch = getattr(self.embedder, "encode_many", None)
                if callable(batch):
                    encoded = batch([m["content"] for m in chunk])
                    if not isinstance(encoded, (list, tuple)) or len(encoded) != len(chunk):
                        raise ValueError("embedder_returned_invalid_batch")
                else:
                    encoded = [self.embedder.encode(m["content"]) for m in chunk]
                vectors.extend(encoded)
            if missing:
                self.index.put_many(actor_id=actor_id, memories=missing, vectors=vectors)
                indexed += len(missing)
            last = memories[-1]
            cursor = {"created_at": last["created_at"], "memory_id": last["memory_id"]}
            # Commit progress only after all vectors for this page are durable.
            self.index.save_backfill_checkpoint(
                actor_id=actor_id, agent_id=agent_id, cursor=cursor, completed=False
            )
            pages += 1
            if len(memories) < page_size:
                self.index.save_backfill_checkpoint(
                    actor_id=actor_id, agent_id=agent_id, cursor=cursor, completed=True
                )
                completed = True
                break
        return {"pages_processed": pages, "memories_indexed": indexed,
                "completed": completed, "checkpoint": cursor,
                "model_id": self.index.model_id}
