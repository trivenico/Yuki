# Audit 087 — Agent Memory Security, Private Context, and Cross-Agent Information Boundaries

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Scope:** local memory, encrypted memory, memory consent/context, vector retrieval, knowledge indexing/graph, conversation context.  
**Outcome:** architecture/security audit; no runtime patch submitted.

## Executive summary

Yuki has meaningful local-memory protections: memory writes are explicit and consent-gated; adding saved memory to model context has a separate session-scoped consent flag; the optional encrypted backend uses AES-256-GCM with a keyring-held key and fails closed when a secure keyring is unavailable; and the context builder bounds and frames retrieved notes as untrusted reference data.

The reviewed storage/retrieval interfaces do not, however, establish a consistent principal boundary for a multi-agent or multi-tenant deployment. The basic memory, vector-memory, and knowledge-graph APIs do not accept an owner, tenant, or agent identity. Safety therefore depends on application composition selecting distinct protected stores and facades for each principal. The reviewed APIs do not enforce that invariant themselves.

This is an interface-level risk assessment. It does not establish that a current deployment shares one store across users, nor does it demonstrate an exploitable cross-user disclosure.

## Files inspected

- `companion/memory.py`
- `companion/secure_memory.py`
- `companion/memory_consent.py`
- `core/vector_memory.py`
- `core/memory_context.py`
- `knowledge/indexer.py`
- `knowledge/knowledge_graph.py`
- `core/conversation_service.py`
- `automation/task_runtime.py`
- `automation/workflow_engine.py`
- `tests/test_companion_secure_memory.py`
- `tests/test_conversation_service.py`

The repository test directory also contains agent coordinator, task journal, and desktop memory tests; this audit did not claim exhaustive review of every memory caller or deployment composition.

## Positive controls observed

1. `CompanionMemory` starts with writes disabled. A caller must explicitly enable memory before saving.
2. Memory context injection is independently disabled by default and requires explicit `enable_context()`.
3. `build_memory_context_system_text` fails closed if consented retrieval or entry validation fails, bounds entry count and output size, and explicitly labels memory as untrusted reference data rather than instructions or policy.
4. `EncryptedMemoryStore` keeps its AES key in an operating-system keyring, authenticates ciphertext with AES-GCM, validates bounded decrypted entries, and does not silently fall back to plaintext when the secure keyring is unavailable.
5. Profile-specific keyring account support and backup key metadata validation exist in the encrypted backend.
6. Conversation persistence and service methods are owner-scoped, and conversation tests exercise owner isolation. This does not automatically scope separate local memory and knowledge stores.

## Findings

### P1 — Memory storage and retrieval APIs do not enforce principal namespaces

`MemoryStore` persists a list of entries and exposes `add`, `list`, `delete`, and `clear` without a principal argument or per-principal authorization check. `VectorMemory` wraps that store and exposes `remember` and `search` without owner, tenant, or agent scope. `CompanionMemory` creates a store using one configured path unless a caller injects a different store.

Consequently, multiple facades or agents configured with the same path can read and mutate the same memory collection. Consent is local to a facade instance; it is not an authorization boundary between principals. The code relies on trusted composition to give each principal a distinct store/path, but that invariant is not represented or checked in these interfaces.

**Risk:** accidental cross-agent or cross-user memory exposure if a shared service constructs multiple facades over the same backing path.

**Required direction:** introduce a trusted principal-bound memory repository/factory. Resolve owner/tenant/agent scope from authenticated runtime context, never from model-supplied arguments. Enforce namespace scope in every read, write, search, delete, export, and clear operation. Include negative cross-principal tests.

### P1 — Knowledge graph operations have no principal-aware access boundary

`KnowledgeGraph` reads and writes a configured JSON file and exposes entity/relation CRUD and export operations without principal or tenant context. Like basic memory storage, this can be safe when a trusted application creates one isolated graph per principal, but the class itself cannot prevent a shared graph instance/path from becoming a cross-user data surface.

**Required direction:** bind graph repositories to an immutable authenticated namespace and ensure exports inherit the same authorization boundary. Add tests that attempt cross-owner entity lookup, relation traversal, mutation, and export.

### P1 — Indexed knowledge has no durable source-to-derived-memory ownership link

`KnowledgeIndexer.index_file` converts file chunks into ordinary `VectorMemory.remember` lesson entries and returns the resulting memory IDs. The persisted memory entry does not carry a structured source identifier, source fingerprint, workspace/principal namespace, index batch, or deletion relationship.

**Risk:** after a source file changes, is removed, or changes ownership, the system cannot reliably identify and revoke every derived memory entry. Filename text is not a durable provenance or authorization record.

**Required direction:** persist an index manifest with immutable source ID, canonical workspace/principal scope, content fingerprint, chunk IDs, indexing version, and trust classification. Updates/deletes should atomically or recoverably reconcile derived chunks. Retrieval must enforce source ACLs and provenance before context injection.

### P2 — Encrypted memory's profile isolation depends on caller configuration

The encrypted backend supports a profile-specific keyring account, but its constructor defaults to the shared `default` account. The default `CompanionMemory` construction path shown in `companion/memory_consent.py` does not itself derive or pass a profile-specific keyring account.

**Risk:** encryption at rest does not by itself establish profile isolation if multiple profiles are configured to use the same encrypted file/keyring account.

**Required direction:** make profile identity a required trusted input in multi-profile desktop composition; derive both the storage path and keyring account from the same verified profile identity. Preserve explicit legacy migration behavior and add integration tests proving profile A cannot decrypt/read profile B's store.

### P2 — Memory context trusts the injected facade to enforce ownership

`build_memory_context_system_text` validates consent state, entry shape, category, and size, but the facade contract has no principal identity or scope assertion. The builder cannot verify that returned entries belong to the authenticated conversation owner or requesting agent.

**Risk:** a miswired facade can inject another principal's saved facts into the current model prompt even though the context builder's format validation succeeds.

**Required direction:** pass an immutable, server-derived principal context to a repository that enforces scope; return provenance-bearing entries; validate owner/tenant/agent scope before constructing model context. Do not accept principal IDs from prompt content or tool output.

### P2 — Knowledge graph export is an additional copy with a separate lifecycle

`KnowledgeGraph.export_obsidian` writes graph content into a caller-supplied directory. The graph class does not establish a policy binding the destination to the source principal, encryption state, export manifest, or deletion/retention lifecycle.

**Required direction:** authorize export destinations at the application boundary, record export provenance, protect output permissions, and define how exports are revoked or deleted when source data is deleted or access is withdrawn.

## Recommended target architecture

1. **Identity binding:** authenticated request establishes immutable user and tenant identity; agent identity is separately authenticated and linked to the invoking principal.
2. **Scoped repository:** every memory/graph/index operation runs through a repository whose namespace is fixed at construction or resolved from trusted execution context.
3. **Provenance:** each memory item records source type, source ID, owner/tenant, agent visibility, trust class, creation/version metadata, and derived-from relationships.
4. **Policy-aware retrieval:** authorization and provenance filtering happen before ranking and before prompt assembly; ranking is never a substitute for access control.
5. **Delegated sharing:** cross-agent sharing requires an explicit, attenuated grant describing audience, allowed data classes, task, purpose, and expiry. Default is private.
6. **Lifecycle propagation:** source edits/deletes, consent withdrawal, account disablement, and grant revocation invalidate derived chunks, caches, exports, and pending context.
7. **Auditability:** record metadata-only access and sharing events without writing private memory contents or secrets into logs.

## Validation plan

Before treating this boundary as production-ready, add and run:

- Two-owner isolation tests across memory CRUD, vector search, graph CRUD/traversal, and export.
- Two-agent tests proving private memory is not visible without an explicit sharing grant.
- Profile A/B encrypted-memory integration tests, including default configuration and backup/restore.
- Source file update/delete tests proving all derived chunks and retrieval caches are invalidated.
- Revocation tests proving an already-created context or queued task cannot use newly revoked data.
- Prompt-injection tests showing memory/document contents remain untrusted and cannot change tool authorization.
- Multi-process tests against the actual configured storage backend.

## Limitations and test status

This audit is based on source inspection of the paths listed above. No exploit was demonstrated, no production deployment was inspected, and no test suite was executed during this audit. Existing tests were inspected as evidence of intended controls; they are not represented as passing in this report.

No runtime patch was submitted because a local change to add an owner parameter would not establish end-to-end isolation across persistence, index manifests, graph export, caches, task execution, and context construction. The next implementation milestone should define and test the principal-scoped repository contract first.
