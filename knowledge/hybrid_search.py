"""Reciprocal-rank fusion over lexical and local semantic retrieval."""
from __future__ import annotations


class HybridKnowledgeSearch:
    def __init__(self, knowledge_engine, vector_store, embedding_backend, *, rrf_k: int = 60):
        if not callable(getattr(knowledge_engine, "search", None)):
            raise TypeError("knowledge_engine must provide search().")
        if not callable(getattr(vector_store, "search", None)):
            raise TypeError("vector_store must provide search().")
        if not callable(getattr(embedding_backend, "embed", None)):
            raise TypeError("embedding_backend must provide embed().")
        if type(rrf_k) is not int or not 1 <= rrf_k <= 1000:
            raise ValueError("rrf_k must be between 1 and 1000.")
        self.knowledge_engine = knowledge_engine
        self.vector_store = vector_store
        self.embedding_backend = embedding_backend
        self.rrf_k = rrf_k

    def search(self, query: str, *, limit: int = 5, candidate_limit: int = 20) -> list[dict]:
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError("query must be non-empty text of at most 2000 characters.")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20.")
        if type(candidate_limit) is not int or not limit <= candidate_limit <= 100:
            raise ValueError("candidate_limit must be between limit and 100.")

        lexical = self.knowledge_engine.search(query, limit=candidate_limit)
        vectors = self.embedding_backend.embed([query])
        if len(vectors) != 1:
            raise ValueError("Embedding backend returned an invalid query batch.")
        semantic = self.vector_store.search(
            vectors[0], model_id=self.embedding_backend.model_id, limit=candidate_limit
        )
        fused: dict[int, dict] = {}
        for rank, item in enumerate(lexical, start=1):
            chunk_id = item.chunk_id
            fused.setdefault(chunk_id, {"chunk_id": chunk_id, "source_id": item.source_id,
                                         "source_name": item.source_name, "source_path": item.source_path,
                                         "content": item.content, "lexical_rank": None,
                                         "semantic_rank": None, "score": 0.0})
            fused[chunk_id]["lexical_rank"] = rank
            fused[chunk_id]["score"] += 1.0 / (self.rrf_k + rank)
        # Semantic results are resolved through the canonical knowledge engine;
        # only indexed chunk IDs can be returned as evidence.
        semantic_ids = [item["chunk_id"] for item in semantic]
        by_id = {item.chunk_id: item for item in self.knowledge_engine.get_chunks(semantic_ids)}
        for rank, item in enumerate(semantic, start=1):
            chunk = by_id.get(item["chunk_id"])
            if chunk is None or chunk.source_id != item["source_id"]:
                continue
            fused.setdefault(chunk.chunk_id, {"chunk_id": chunk.chunk_id, "source_id": chunk.source_id,
                                               "source_name": chunk.source_name, "source_path": chunk.source_path,
                                               "content": chunk.content, "lexical_rank": None,
                                               "semantic_rank": None, "score": 0.0})
            fused[chunk.chunk_id]["semantic_rank"] = rank
            fused[chunk.chunk_id]["score"] += 1.0 / (self.rrf_k + rank)
        return sorted(fused.values(), key=lambda item: (-item["score"], item["chunk_id"]))[:limit]
