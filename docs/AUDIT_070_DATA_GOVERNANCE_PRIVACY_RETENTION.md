# Audit 070 — Data Governance, Privacy, Retention & User Data Lifecycle

**Scope:** ownership, persistence, deletion, export, retention, backup and lifecycle behavior across conversations, local memory, knowledge indexing, OAuth credentials and approval audit records.

**Review basis:** source inspection on `main` of `core/conversation_store.py`, `companion/memory.py`, `core/vector_memory.py`, `knowledge/indexer.py`, `knowledge/knowledge_graph.py`, `core/oauth_credential_vault.py`, `core/oauth_disconnect_service.py`, `core/approval_audit.py`, `api/conversations.py`, `api/oauth_connections.py`, and `api/runtime.py`. Candidate test paths for memory and knowledge indexing were not present at the exact conventional paths checked. This is a focused code review, not a complete inventory of every application data sink or deployment backup.

## Positive controls observed

- Conversation records are owner-scoped in SQL queries; messages and generation records are linked to conversations with foreign keys and cascade declarations.
- Conversation writes use transactions, version checks, idempotency keys, bounded message sizes and owner-scoped access.
- OAuth credentials are encrypted through a host-provided AEAD interface; associated data binds owner/provider/account/token kind. Vault methods are owner-scoped and tokens are excluded from dataclass repr.
- OAuth disconnect has a version fence in the vault/service path (see Audit 068).
- Local memory has bounded entry count and text size, explicit `delete` and `clear` methods, atomic JSON writes and file locking.
- Knowledge indexing requires an explicit workspace path, constrains file types and size, and bounds imported content.
- Approval audit records store hashes for actor and pending identifiers rather than plaintext values.

## Findings

### P1 — Conversation archive is not deletion; no deletion/export contract in the inspected store

`ConversationStore` implements `archive_conversation`, `list_conversations`, `list_messages`, and persistence operations. The inspected class has no conversation deletion method and no owner-scoped export method. Archiving marks the conversation as archived; it does not remove message content, generation checkpoints, or linked records. The inspected API path exposes listing and creation, but no delete/export route was identified in the reviewed conversation API.

**Impact:** a user or operator cannot rely on the inspected conversation persistence API to fulfill a complete delete or portability request. Archived data remains persisted. This is a confirmed capability gap in the reviewed store/API paths, not a claim that no other application mechanism exists.

**Recommendation:** define an owner-authenticated lifecycle service with preview/count, export, deletion and status reporting. Delete transactionally by owner and conversation ID, verify cascades for messages, generation runs and leases, and test that a different owner cannot delete/export. Define whether audit evidence must be retained separately and how that exception is communicated.

### P1 — Local memory deletion is logical file rewrite, not guaranteed physical erasure

`MemoryStore.delete` and `clear` rewrite the JSON document using an atomic replacement. This removes entries from the current logical file, but does not establish erasure from filesystem snapshots, backups, journaling, cloud sync, or storage media. The store itself does not implement retention expiry or an export/subject-access interface.

**Recommendation:** document the exact deletion guarantee; inventory backups/sync locations and retention; provide user-scoped export/delete orchestration across memory and downstream indexes; use encryption-key destruction where appropriate for data at rest, with explicit backup expiry. Do not describe ordinary file rewrite as secure erase.

### P1 — Knowledge indexing loses source lifecycle/provenance linkage

`KnowledgeIndexer.index_file` copies bounded chunks from a workspace file into `VectorMemory.remember` as lesson text prefixed with the source filename. The stored memory schema does not retain a structured source path, source fingerprint, index batch ID, or deletion relationship. Consequently, the inspected interface does not provide a reliable way to identify and remove all derived chunks when the source file is changed or deleted.

**Recommendation:** persist structured source identity, owner/workspace scope, content fingerprint, import timestamp, trust classification, and batch/chunk IDs. Implement reindex and source-delete operations that atomically remove/reconcile all derived chunks. Keep imported content distinguishable from user-authored memory.

### P2 — Knowledge graph export is an uncontrolled copy boundary in the inspected class

`KnowledgeGraph.export_obsidian` writes entity names, descriptions and relations to a caller-supplied directory. The method does not itself enforce a configured export root, destination permissions, encryption, or an export manifest. Callers may intentionally use an external/synced directory; the reviewed method does not establish what happens to those copies when source graph data is later deleted.

**Recommendation:** make export an explicit user-authorized operation, constrain or confirm destination, create private files, emit a manifest, warn about third-party sync/backup copies, and document independent deletion responsibility for exported files.

### P2 — Retention and backup lifecycle are not expressed by the reviewed persistence contracts

The inspected conversation, memory, knowledge graph, OAuth vault and approval audit stores enforce size/ownership or cryptographic controls, but the reviewed class contracts do not define a unified retention schedule, backup expiry, legal hold, user export, or deletion verification across stores. SQLite WAL and external backup handling require deployment-level review; source-level row deletion alone does not guarantee historical page/WAL/backup erasure.

**Recommendation:** create a data inventory and policy matrix by data class (conversation content, derived memory/indexes, OAuth tokens, security audit, operational metadata, exports/backups). Define owner, purpose, legal basis, retention, deletion SLA, backup expiry, access controls and exceptions. Keep security audit retention distinct from user content retention and minimize retained identifiers.

## Remediation decision

No runtime patch was submitted in this audit. A safe deletion feature must coordinate conversation rows, generation runs/leases, derived memory/index chunks, graph exports, backups, and any audit-retention exception. Adding a DELETE route without a complete lifecycle contract risks partial deletion and misleading success responses. Follow-up implementation should be staged:
1. data inventory and owner-scoped export/delete service contract;
2. conversation transactional deletion with cascade and cross-owner regression tests;
3. structured source provenance and derived-index deletion;
4. memory expiry and user-facing lifecycle orchestration;
5. export destination controls and backup/retention policy enforcement.

## Validation status

- Source inspection completed for the paths listed above.
- Existing conversation and OAuth tests were inspected; memory/index test candidates were not found at the exact paths checked.
- **No tests were executed.**
- No deployment backup, filesystem snapshot, cloud sync, or physical-erasure validation was performed.
- No application runtime code was changed in this audit.
