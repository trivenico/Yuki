# Audit 087 — Agent Memory Security, Private Context, and Cross-Agent Information Boundaries

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Scope:** companion memory and consent, encrypted desktop memory, memory context assembly, vector memory, knowledge indexing/graph, authenticated conversations, and specialist workspace read tools.

## Executive summary

Yuki has useful privacy controls: desktop memory is encrypted with AES-256-GCM and a profile-scoped OS-keyring key; the desktop composition validates ciphertext before exposing memory; memory writes and context injection are session-consent gated; disabling context prevents retrieval; memory context is bounded and framed as untrusted reference data; conversation history is owner-scoped; specialist read tools use a trusted principal resolver, explicit read-only registry, permission checks, secret-path exclusions, audit preflight, and bounded outputs.

A confirmed file-read boundary weakness in the specialist workspace tools is addressed in implementation PR #1154. Broader architecture still needs tenant/agent namespaces, typed provenance, source-to-derived-memory deletion, and per-agent retrieval policy.

## Findings

### P1 — Specialist workspace read had a check/use race and unbounded read after size preflight

`AuthorizedReadOnlyTools._safe_path` resolved a path under the workspace, then `_read_file` checked file metadata and reopened it with `Path.read_text()`. A concurrent replacement between validation and open could change the filesystem object; a hard link could expose content whose inode also exists outside the workspace. A concurrent file growth could also invalidate the pre-read size check and cause an oversized allocation before the post-read length check.

**Fix submitted in PR #1154:** open once with `O_NOFOLLOW`, verify the opened descriptor is a regular single-link file with `fstat`, read at most `MAX_FILE_BYTES + 1`, then decode UTF-8. Regression tests cover symlink replacement after validation and hard-linked files.

### P1 — Memory and vector retrieval APIs do not carry tenant/agent identity

`MemoryStore`, `CompanionMemory`, and `VectorMemory` expose local CRUD/search interfaces without an owner, tenant, or agent namespace parameter. Desktop profile isolation is provided by composition and profile-specific storage/keyring, but the underlying APIs do not enforce isolation if a shared instance or path is injected into multiple agents or application tenants.

**Recommendation:** use a trusted memory gateway that derives tenant, owner, agent, and session from authenticated runtime context. Store namespace fields in durable records and include them in every list/search/update/delete predicate. Do not accept namespace IDs from model-generated tool arguments.

### P1 — Knowledge indexing creates derived memories without durable source lineage

`KnowledgeIndexer.index_file` writes filename-prefixed text chunks into `VectorMemory` and returns memory IDs to the caller. It does not persist a durable source record, canonical source path, content fingerprint, index batch ID, or source-to-chunk relation. Updating or deleting the source therefore cannot reliably identify and remove all derived chunks across stores.

**Recommendation:** maintain an index manifest with owner/tenant, source ID, canonical path, content hash/version, chunk IDs, trust classification, and creation time. Source update/delete should transactionally enqueue reindex/deletion work and reconcile orphaned chunks.

### P2 — Memory consent is a session-local facade flag, not a per-agent data-sharing policy

`CompanionMemory` gates write/context use with in-memory booleans, and context entries are retrieved from the same store. This is appropriate for one local companion session, but it does not express whether a specific specialist may retrieve a particular category or memory record, or whether a user-approved memory may be shared with an external provider.

**Recommendation:** add purpose-bound consent grants with agent/task/provider audience, categories, retention, and revocation. Keep the current explicit session consent as an additional prerequisite rather than replacing it.

### P2 — Context trust is represented by prompt framing, not typed provenance

`build_memory_context_system_text` bounds entries and instructs the model to treat them as untrusted data. Specialist workspace tools and results also mark content as untrusted. However, trust/provenance metadata is flattened into strings/JSON before provider context assembly, making it harder for later stages to preserve origin and enforce source-specific policies.

**Recommendation:** carry typed context segments with source ID, owner/tenant, agent visibility, trust class, content hash, and sensitivity label through retrieval, planning, prompt assembly, and audit. Prompt labels are defense in depth, never authorization.

### P2 — Retrieval behavior is not a unified relevance/freshness/conflict policy

The companion memory facade ranks lexical overlap over a bounded latest-entry slice; VectorMemory uses lexical overlap over up to 200 records; knowledge indexing truncates source content at 30,000 characters. These are bounded local mechanisms, not a unified retrieval planner with freshness, source authority, contradiction handling, or explicit truncation markers.

**Recommendation:** implement a retrieval planner that combines access control before ranking, freshness and provenance, source authority, relevance, conflict detection, and explicit truncation/coverage metadata.

### P2 — Deletion does not yet establish complete derived-copy lifecycle

Local memory deletion rewrites the store, but backups, encrypted archives, derived vector chunks, knowledge graph exports, conversation references, and cached context are separate lifecycle surfaces. This review does not establish complete deletion across all of them.

**Recommendation:** implement an owner-scoped deletion workflow with source/derived-copy inventory, tombstones, cache invalidation, backup expiry, export lifecycle, and auditable completion status. Define lawful audit-retention exceptions separately.

## Existing positive controls

- Desktop memory uses authenticated encryption and profile-scoped OS-keyring accounts.
- Missing keys/corrupt ciphertext fail closed rather than silently recreating or falling back to plaintext.
- Desktop composition checks encrypted memory integrity before use.
- Memory writes require explicit session consent; context injection requires separate consent.
- Memory context is bounded and treated as untrusted reference data.
- Conversation history/store operations are owner-scoped.
- Specialist workspace tools are read-only and use a trusted principal resolver.
- Specialist tool names and permissions are explicit; model-generated tool calls cannot invent tools.
- Secret-like paths, traversal, symlinks escaping the workspace, and oversized results are rejected.
- Audit preflight must succeed before workspace handlers execute.
- Specialist evidence and generated outputs retain untrusted/generated provenance labels.

## Recommended private-context architecture

1. **Identity context:** trusted owner, tenant, agent, task, session, and provider identity.
2. **Memory gateway:** all retrieval and writes require authenticated namespace and policy context.
3. **Purpose-bound consent:** category, task purpose, agent/provider audience, retention, and revocation.
4. **Provenance ledger:** source IDs, content hashes, source versions, trust, sensitivity, and derived-copy edges.
5. **Policy-first retrieval:** enforce visibility before ranking or embedding search.
6. **Typed context segments:** preserve provenance and sensitivity through compaction and provider adapters.
7. **Derived data lifecycle:** update/delete source propagates to chunks, graph exports, caches, and backups.
8. **Agent isolation:** specialists receive minimum task-specific context, never a default full-memory dump.
9. **Audit:** record access decision and source references without logging private content.

## Required regression/adversarial tests

- Cross-tenant and cross-agent memory search/list/write/delete are denied.
- A specialist cannot retrieve personal memory unless an explicit purpose-bound grant allows it.
- Consent revocation blocks subsequent retrieval and provider context injection.
- Workspace file replacement with symlink/hard link between validation and open is rejected.
- Workspace file growth during read never exceeds the configured byte allocation.
- Indexing records durable source lineage and deletion removes all derived chunks.
- Context compaction preserves source IDs, trust class, sensitivity, and truncation indicators.
- Prompt-injection text in memory or workspace files cannot change tool authorization.
- Backup restore and deletion reconcile derived copies and key custody.
- Shared-memory deployment cannot leak across concurrent agent/tenant requests.

## Validation limits

Source inspection only. Implementation PR #1154 adds regression tests but they were not executed. No cross-tenant multi-process memory deployment, real OS keyring, backup restore, concurrent filesystem mutation stress, source deletion reconciliation, or provider-context exfiltration test was performed. Desktop profile scoping is a composition guarantee in the reviewed path; this audit does not claim the raw memory store API independently enforces profile identity.
