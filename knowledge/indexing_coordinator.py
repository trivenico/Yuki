"""Coordinate document parsing, canonical ingestion and semantic indexing."""
from __future__ import annotations

from knowledge.document_parsers import DocumentParser


class SemanticIndexCoordinator:
    """Keep parsed source text and its semantic vectors aligned.

    Embeddings are generated before canonical source replacement. If parsing or
    embedding fails, the existing lexical and semantic source remains untouched.
    """

    def __init__(self, knowledge_engine, vector_store, embedding_backend, *,
                 parser=None, batch_size: int = 64):
        for name, obj, method in (
            ("knowledge_engine", knowledge_engine, "ingest_extracted_text"),
            ("vector_store", vector_store, "upsert_many"),
            ("embedding_backend", embedding_backend, "embed"),
        ):
            if not callable(getattr(obj, method, None)):
                raise TypeError(f"{name} must provide {method}().")
        if not callable(getattr(knowledge_engine, "chunk_text", None)):
            raise TypeError("knowledge_engine must provide chunk_text().")
        if type(batch_size) is not int or not 1 <= batch_size <= 256:
            raise ValueError("batch_size must be between 1 and 256.")
        self.knowledge_engine = knowledge_engine
        self.vector_store = vector_store
        self.embedding_backend = embedding_backend
        self.parser = parser or DocumentParser()
        if not callable(getattr(self.parser, "parse_file", None)):
            raise TypeError("parser must provide parse_file().")
        self.batch_size = batch_size

    def index_file(self, path, *, workspace) -> dict:
        document = self.parser.parse_file(path, workspace=workspace)
        prepared_chunks = self.knowledge_engine.chunk_text(document.text)

        # Complete all potentially expensive model calls before replacing canonical
        # chunks. This preserves the old index when a local model is unavailable.
        prepared_vectors = []
        for offset in range(0, len(prepared_chunks), self.batch_size):
            batch = prepared_chunks[offset:offset + self.batch_size]
            vectors = self.embedding_backend.embed(batch)
            if len(vectors) != len(batch):
                raise RuntimeError("Embedding backend returned an unexpected batch size.")
            prepared_vectors.extend(vectors)

        ingestion = self.knowledge_engine.ingest_extracted_text(
            source_path=document.source_path, workspace=workspace,
            source_sha256=document.source_sha256, text=document.text,
        )
        source_id = ingestion["source_id"]
        chunks = self.knowledge_engine.get_source_chunks(source_id)
        if [chunk.content for chunk in chunks] != prepared_chunks:
            raise RuntimeError("Canonical chunks differ from the prepared embedding input.")

        indexed = 0
        for offset in range(0, len(chunks), self.batch_size):
            batch = chunks[offset:offset + self.batch_size]
            vectors = prepared_vectors[offset:offset + self.batch_size]
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
            "document_format": document.format,
            "page_count": document.page_count,
        }

    def remove_source(self, source_id: str) -> bool:
        removed = self.knowledge_engine.remove_source(source_id)
        self.vector_store.delete_source(
            source_id, model_id=self.embedding_backend.model_id
        )
        return removed
