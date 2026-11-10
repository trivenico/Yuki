# Persistent semantic memory vector index

Yuki's SQLite memory store remains the authority for authorization and visible
memory records. `SQLiteEmbeddingIndex` is a rebuildable cache only: it stores
float32 vectors keyed by actor, memory ID, embedding model identity, and a
SHA-256 digest of the memory content.

## Enable

```python
from core.agent_memory import AgentMemoryStore
from core.embedding_index import SQLiteEmbeddingIndex
from core.semantic_memory import SemanticMemoryRetriever
from providers.local_embeddings import HuggingFaceLocalEmbedder

store = AgentMemoryStore(database_path, authorize=authorize)
embedder = HuggingFaceLocalEmbedder(local_model_directory)
index = SQLiteEmbeddingIndex(
    database_path, model_id="sentence-transformer-model@revision-or-digest"
)
retriever = SemanticMemoryRetriever(store, embedder, vector_index=index)
```

Use an immutable model revision or artifact digest as `model_id`. A model
upgrade gets a new namespace and rebuilds vectors without corrupting the
previous model's cache. The first search embeds missing candidates in a batch;
subsequent searches embed only the query unless memory content changes.

## Security and correctness

- Every search first calls the existing permission-aware memory store. The
  vector index only accepts IDs from that authorized result set.
- Cache keys include actor ID and model ID. Shared memories are still visible
  only when the memory store authorizes and returns them.
- Content SHA-256 detects edits and invalidates stale vectors.
- Vectors are stored as bounded float32 blobs with dimension and finite-value
  checks; no pickle or executable serialization is used.
- The index is derived data and can be deleted/rebuilt from the source memories.
- Deleting a memory should also call `index.delete_memory(actor_id, memory_id)`
  in the same application workflow. Expired memory cache cleanup is a follow-up.

## Current scale and next work

This PR caches vectors persistently but still scores the authorized candidate
window in Python. It is a correctness-first first step, not an ANN index.
Next: integrate cache deletion into memory deletion/pruning, add an embedding
backfill command, benchmark real local models and SQLite at increasing corpus
sizes, then evaluate SQLite vector extensions or a local ANN backend only if
benchmarks justify the added dependency.
