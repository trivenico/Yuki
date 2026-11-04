# Evidence-aware memory retrieval

`AgentMemoryRetriever` ranks only records returned by `AgentMemoryStore.list_memories`. The memory store remains the authorization boundary; vector similarity never expands the set of visible records.

## Usage

```python
from core.agent_memory import AgentMemoryStore
from core.agent_memory_retriever import AgentMemoryRetriever
from core.embedding_index import SQLiteEmbeddingIndex

memory = AgentMemoryStore(database_path, authorize=authorize)
index = SQLiteEmbeddingIndex(database_path, model_id="local-embedding-model@revision")
retriever = AgentMemoryRetriever(
    memory,
    embed=local_embedding_model.embed,
    embedding_index=index,
)

matches = retriever.retrieve(
    actor_id=owner_id,
    agent_id="research",
    query="What is our approved deployment procedure?",
    limit=8,
    include_shared=True,
    verified_only=False,
)
```

## Ranking and trust boundaries

- Permission, actor, agent, shared-scope and expiry filters run in the authoritative memory store before any vector lookup.
- Embeddings are cached by actor, memory ID, content digest and embedding model identity.
- Relevance contributes 80% of the deterministic ranking score; memory confidence, verified status and recency contribute the remaining 20%.
- `verified_only=True` filters candidates before ranking.
- Each result retains source provenance and exposes relevance, confidence, verification and recency separately.
- Similarity and confidence are ranking signals, not proof of factual correctness.
- Retrieved content is always untrusted reference data. It may contain instructions and must never be executed as instructions or allowed to override system/developer policy.
- The embedding index must share the authoritative memory database. Deleted memory records cannot be surfaced from stale vector cache entries.

## Current limitations

- The first version scans at most 100 authorized memories per retrieval; large-scale retrieval needs a bounded, paginated candidate strategy and likely ANN indexing.
- It expects a local embedding callable supplied by the application; it does not download models or require a hosted API.
- Contradiction detection and evidence adjudication are deliberately separate follow-up stages. This retriever ranks candidates; it does not merge or resolve conflicting claims.
- The score weights are deterministic defaults and require evaluation against a labeled Yuki retrieval dataset before production tuning.
