# Audit 081 — Agent Memory Isolation, Knowledge Sharing, and Long-Term Intelligence Security

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Scope:** local memory storage, lexical retrieval, knowledge indexing, knowledge graph, conversation memory integration, ownership, provenance, trust, and deletion lifecycle.

## Executive summary

Yuki has a bounded local memory store with explicit add/list/delete/clear operations, atomic JSON writes, file locking, and a lexical retrieval layer. The knowledge indexer constrains file types, workspace containment, file size, and chunk count. These are useful foundations for a personal assistant.

The inspected contracts are not yet sufficient for multi-user/multi-agent memory isolation or trustworthy long-term knowledge lifecycle. Memories have no first-class owner/agent namespace, trust classification, provenance record, source-to-chunk relationship, or deletion propagation. Retrieval also examines only a subset of stored memories.

## Findings

### P1 — Basic memory API has no owner or agent namespace

`companion/memory.py::MemoryStore` accepts a file path and exposes add/list/delete/clear without an owner, tenant, or agent parameter. `core/vector_memory.py` wraps the same store without adding an authorization boundary.

This is suitable for a deliberately single-user local store. It is unsafe to expose one shared instance as a multi-user or multi-agent memory service. Isolation currently depends on composition selecting distinct protected paths/instances, not on the memory API enforcing principal ownership.

**Recommendation:** introduce a memory gateway that derives owner/tenant/agent namespace from authenticated context, not caller-supplied text; enforce ACLs at read/write/delete; separate private, team-shared, and globally curated knowledge; and test cross-namespace access.

### P1 — Indexed content has no structured provenance or trust metadata

`KnowledgeIndexer.index_file` imports document chunks through `VectorMemory.remember(..., category="lesson")` and prefixes each chunk with a human-readable filename. The stored memory schema contains ID, timestamp, category, and text. It does not retain canonical source identity, source fingerprint, chunk index, indexing batch, trust class, or source update timestamp.

Consequently, downstream retrieval cannot reliably distinguish a user-authored preference from an untrusted document instruction or an imported technical reference. A filename prefix is not a security label and can be forged by document content.

**Recommendation:** store immutable provenance fields (source type, stable source ID, content hash, chunk ordinal, ingestion time, importer identity) separately from content. Assign explicit trust classes and treat retrieved document text as untrusted evidence, never as policy or system instruction.

### P1 — Source deletion/update cannot reliably remove derived memories

The indexer returns memory IDs to its caller, but the inspected store has no durable source-to-memory relationship or operation to replace/remove all chunks for a source. If a file changes or is deleted, old chunks can remain available to retrieval and contradict newer content.

**Recommendation:** make indexing an atomic versioned batch keyed by source ID and content hash. Commit a new batch, then retire the previous batch transactionally. Provide source deletion that removes or tombstones all derived chunks and propagates to caches, graph exports, and downstream stores.

### P2 — Retrieval silently searches only the latest 200 memories

`MemoryStore.MAX_ENTRIES` permits 2,000 entries, while `VectorMemory.search` calls `store.list(limit=200)`. This means older entries can remain stored and visible in list operations but are excluded from search without an explicit signal or pagination strategy. The behavior is a recall limitation, not data loss.

**Recommendation:** add indexed retrieval/pagination or an explicit configurable candidate budget; expose whether search was exhaustive or truncated; rank across the intended namespace rather than only the newest slice.

### P2 — Chunking silently truncates large supported files

The indexer permits files up to 500 KB but creates chunks from only the first 30,000 characters. It returns a successful result with chunk count and IDs but no truncation indicator or indexed byte/character range. This can omit relevant content without alerting the caller.

**Recommendation:** return explicit truncation metadata and either index the complete bounded file in batches or require the caller to acknowledge partial ingestion.

### P2 — Trust and retention are not unified across memory and graph

Memory deletion rewrites the JSON store, but this does not prove physical erasure from backups, snapshots, or filesystem journaling. The knowledge graph has its own independent file and CRUD lifecycle. The reviewed interfaces do not establish a unified retention, export, deletion, or legal-hold contract across memories, graph nodes, indexed chunks, and exports.

**Recommendation:** define a data lifecycle service with source-of-truth ownership, retention, export, logical deletion, derived-data tombstones, and documented backup expiry. Do not claim secure erasure based only on rewriting a local JSON file.

### P2 — Knowledge graph lacks principal-level access controls in its API

`KnowledgeGraph` exposes add/list/relation/export operations without owner/tenant/agent arguments. As with MemoryStore, it can serve a single-user local profile, but a shared multi-user deployment must isolate instances and enforce access in a trusted gateway.

## Existing positive controls

- Memory entries are bounded by count and text length.
- Memory writes use atomic JSON replacement and a file lock.
- Corrupt memory files fail without overwriting existing data.
- Memory categories are constrained to a small allow-list.
- Indexer supports a bounded set of text file types and caps file size.
- Indexer resolves the target path and enforces workspace containment.
- Knowledge graph validates entity/relation structures and bounds entity/relation counts.
- Conversation history is owner-scoped in the conversation store, but this does not automatically namespace separate memory stores.

## Recommended Yuki memory architecture

1. **Memory gateway:** all reads/writes/deletes require authenticated owner, tenant, and agent context.
2. **Namespace model:** private user memory, private agent working memory, explicitly shared team memory, and curated global knowledge are distinct.
3. **Provenance envelope:** source ID/hash, author/importer, timestamp, trust class, content type, and derivation lineage are structured fields.
4. **Retrieval policy:** authorize before retrieval, filter by namespace before ranking, and label retrieved untrusted content.
5. **Memory poisoning defense:** imported text cannot change policy, permissions, system prompts, or capability grants.
6. **Versioned indexing:** source-level batches with atomic replacement and tombstones for removed content.
7. **Lifecycle service:** export, retention, deletion propagation, derived-memory cleanup, and backup expiry.
8. **Observability:** record provenance IDs and policy decisions without logging sensitive memory contents.

## Required regression/adversarial tests

- User A cannot retrieve, update, or delete User B's memory.
- Agent A cannot access Agent B's private working memory absent an explicit share grant.
- Untrusted indexed text containing instructions remains labeled as untrusted after retrieval.
- Updating a source replaces its old chunks; deleting a source makes all derived chunks unavailable.
- Retrieval reports candidate truncation or searches the complete authorized index.
- Large-file partial indexing is explicit and cannot silently masquerade as full ingestion.
- Concurrent indexing of the same source cannot leave mixed versions.
- Memory deletion propagates to caches and derived stores under the documented retention contract.

## Validation limits

Source inspection only. No runtime code was changed and no tests were executed. No multi-user deployment, adversarial memory-poisoning run, concurrent indexing test, backup erasure test, or full derived-data deletion test was performed. This audit does not claim a production-ready multi-agent memory isolation system.
