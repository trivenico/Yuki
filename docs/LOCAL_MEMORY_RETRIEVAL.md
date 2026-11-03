# Local Memory Retrieval — Engineering Note

## Current foundations

Yuki already has an opt-in local memory store in `companion/memory.py` and a retrieval facade in `core/vector_memory.py`. The store persists bounded JSON entries and supports explicit deletion. The retrieval facade previously used English-only ASCII tokenization and Jaccard overlap; despite the historical class name, it does not use vector embeddings.

## This change

The retrieval facade now uses Unicode-aware tokenization and deterministic BM25-style lexical ranking over the existing bounded local corpus. It adds category filtering and explicit list/forget convenience methods while preserving the existing `remember` and `search(query, limit=...)` call shape.

No new database, embedding model, cloud API, telemetry, or background memory capture is introduced.

## Privacy and safety boundaries

- Memory writes remain explicit opt-in calls; conversation messages are not automatically persisted as memories.
- The existing local store remains the source of truth.
- Search returns only stored entries selected by the caller's query and optional category.
- Deletion is explicit by opaque memory ID.
- This is lexical retrieval, not semantic vector search and not model training.
- Multi-user web memory isolation is not claimed by this facade. Do not expose one process-global memory instance to multiple authenticated users.

## Follow-up architecture

Before wiring memory into the conversational prompt, implement a request-scoped memory service with:
1. A per-user/per-workspace storage namespace derived from verified server identity.
2. Explicit consent and memory categories, including a no-memory mode.
3. Bounded retrieval budgets and source IDs in prompt context.
4. Prompt-injection treatment: retrieved memory is untrusted context, never tool authorization.
5. Export, inspect, edit, and delete APIs with audit events.
6. Tests proving cross-user isolation and that memory is not written from ordinary conversation by default.

Only after these controls exist should the conversation runtime retrieve relevant memory automatically. Semantic embeddings can be an optional local index later; lexical fallback must continue to work offline.
