"""Coordinate canonical document ingestion with local embedding persistence."""
from __future__ import annotations


class SemanticIndexCoordinator:
    """Keep a source's semantic vectors aligned with its canonical knowledge chunks.

    Embeddings are generated and upserted before stale vectors are pruned. If
    embedding generation fails, the previous semantic index remains available.
    """

    def __init__(self, knowledge_engine, vector_store, embedding_backend, *, batch_size: int = 64):
        for name, obj, method in (
            ("knowledge_engine", knowledge_engine, "ingest_file"),
            ("vector_store", vector_store, "upsert_many"),
            ("embedding_backend", embedding_backend, "embed"),
        ):
            if not callable(getattr(obj, method, None)):
                raise TypeError(f"{name} must provide {method}().")
        if type(batch_size) is not int or not 1 <= batch_size <= 256:
            raise ValueError("batch_size must be between 1 and 256.")
        self.knowledge_engine = knowledge_engine
        self.vector_store = vector_store
        self.embedding_backend = embedding_backend
        self.batch_size = batch_size

    def index_file(self, path, *, workspace) -> dict:
        ingestion = self.knowledge_engine.ingest_file(path, workspace=workspace)
        source_id = ingestion["source_id"]
        chunks = self.knowledge_engine.get_source_chunks(source_id)
        if not chunks:
            raise RuntimeError("Ingested source has no canonical chunks.")

        indexed = 0
        for offset in range(0, len(chunks), self.batch_size):
            batch = chunks[offset:offset + self.batch_size]
            vectors = self.embedding_backend.embed([chunk.content for chunk in batch])
            if len(vectors) != len(batch):
                raise RuntimeError("Embedding backend returned an unexpected batch size.")
            records = [
                {"chunk_id": chunk.chunk_id, "source_id": chunk.source_id, "vector": vector}
                for chunk, vector in zip(batch, vectors)
            ]
            indexed += self.vector_store.upsert_many(
                records, model_id=self.embedding_backend.model_id
            )

        removed = self.vector_store.prune_source(
            source_id, [chunk.chunk_id for chunk in chunks],
            model_id=self.embedding_backend.model_id,
        )
        return {
            **ingestion,
            "vectors_indexed": indexed,
            "stale_vectors_removed": removed,
            "embedding_model": self.embedding_backend.model_id,
        }

    def remove_source(self, source_id: str) -> bool:
        """Remove canonical content and its vectors for this embedding model."""
        removed = self.knowledge_engine.remove_source(source_id)
        self.vector_store.delete_source(
            source_id, model_id=self.embedding_backend.model_id
        )
        return removed
