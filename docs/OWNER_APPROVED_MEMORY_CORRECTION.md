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


## Owner-approved correction

The memory store also exposes an explicit `correct(...)` operation. It requires the dedicated `agent_memory:correct` authorization grant; correcting a shared memory additionally requires `agent_memory:share`.

A correction atomically creates a replacement already marked `approved` and `verified`, because the correction operation itself requires explicit owner authorization and a rationale.
1. Marks the active predecessor with a `superseded_by` link and timestamp.
2. Inserts a new verified replacement with a new memory ID and retained category/scope.
3. Writes an audit event containing actor, agent, predecessor, replacement, reason and approval timestamp.
4. Invalidates the predecessor's embedding cache rows in the same SQLite transaction.

Normal memory listing, paginated listing, lexical search and semantic retrieval expose only active (not superseded) records. The predecessor remains stored for audit/history. Correction audit events are protected against UPDATE and DELETE by SQLite triggers. A correction is not a general-purpose edit: it is an owner-authorized assertion that the replacement has been reviewed. The caller must provide a meaningful source and reason.
