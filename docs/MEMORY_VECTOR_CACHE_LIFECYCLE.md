# Memory and embedding-cache lifecycle

The semantic embedding index is derived data. The authoritative source remains
`agent_memories`, and cache invalidation now follows source deletion in the
same SQLite transaction.

## Deletion

An authorized `AgentMemoryStore.delete` first deletes the actor/agent-scoped
source record. If that delete succeeds and the embedding table exists, vectors
for the same actor and memory ID are deleted before the transaction commits.
If cache cleanup fails, the transaction rolls back the source deletion too.

The optional-table check preserves compatibility with databases that have not
yet initialized the embedding index.

## Expiration

`prune_expired` selects only the requesting actor's expired memory IDs, deletes
those source records, and removes matching cached vectors in the same
transaction. It does not touch another actor's rows or unexpired records.

## Rebuildability

The cache remains disposable and model-versioned. A missing or invalidated
vector is recomputed from the current authorized source record on the next
semantic retrieval. The cache is not an independent source of memory truth.
