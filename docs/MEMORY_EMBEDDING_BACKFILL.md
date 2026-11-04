# Resumable local memory embedding backfill

`MemoryEmbeddingBackfill` incrementally fills the model-versioned SQLite
embedding cache from records returned by the permission-aware memory store.

## Run

```python
from core.agent_memory import AgentMemoryStore
from core.embedding_index import SQLiteEmbeddingIndex
from core.memory_embedding_backfill import MemoryEmbeddingBackfill
from providers.local_embeddings import HuggingFaceLocalEmbedder

store = AgentMemoryStore(database_path, authorize=authorize)
index = SQLiteEmbeddingIndex(database_path, model_id="model@immutable-revision")
embedder = HuggingFaceLocalEmbedder(local_model_directory)
job = MemoryEmbeddingBackfill(store, embedder, index)

# Process at most 5 pages; call again to resume from the durable checkpoint.
result = job.run(
    actor_id="owner", agent_id="research",
    page_size=50, max_pages=5,
)
```

## Guarantees

- Reads use the memory store's normal actor/agent authorization and expiry
  filters; the backfill never scans raw memory tables.
- Keyset pagination uses the stable `(created_at, memory_id)` ordering.
- Checkpoints are stored in SQLite and scoped to actor, agent and model ID.
- Embedding vectors are committed before advancing a page checkpoint. A crash
  may repeat a page but cannot advance past vectors that were not committed.
- Existing content-digest matches are reused; a restart does not recompute them.
- Model upgrades use a new model ID and therefore get an independent backfill.
- Page size, number of pages per invocation and embedding text batch sizes are
  bounded. No network access is initiated by the backfill.

The caller must have the memory-read permission. Backfill is a maintenance
operation; it does not alter, verify, or promote memory content.
