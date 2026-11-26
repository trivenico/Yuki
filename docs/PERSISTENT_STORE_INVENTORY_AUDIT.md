# Persistent Store Inventory and Lifecycle Audit

**Review date:** 2026-11-26  
**Branch:** `research/reference-projects-integration-assessment`  
**Scope:** Targeted source inspection of first-party persistence implementations and their immediate composition seams. This is not yet a complete repository-wide inventory, a test result, or a security certification.

## Executive finding

Yuki does not currently expose one application-wide owner for all persistent state. The inspected code contains multiple independently constructed SQLite stores and a pooled PostgreSQL conversation store. The workflow/task journal inspected on this branch is SQLite-backed; a separate PostgreSQL workflow/control-plane repository/runtime was not found in the reviewed branch tree. Their lifecycle and security properties differ. Closing the conversation runtime is not proof that all writers have stopped, and the existing per-database backup primitives do not by themselves establish a consistent multi-store snapshot.

Do not implement a global maintenance lock until the remaining stores and all writer entry points are enumerated. Do not claim backup/migration protection for stores that have not been inventoried and instrumented.

## Inspected store inventory

| Store / implementation | Persistence and ownership | Transaction / lifecycle observations | Maintenance status |
|---|---|---|---|
| Built-in authentication — `core/auth.py:BuiltInAuthService` | SQLite users and session hashes; database path supplied by composition | Short-lived connections; account bootstrap and authentication mutate state. Uses `prepare_private_sqlite_path()`; WAL enabled. No store-wide close/maintenance contract observed in inspected portion. | Not shown participating in a global gate or coordinated backup. Parent-directory safety inherits the known helper limitations. |
| Device pairing — `core/device_pairing.py:DevicePairingService` | SQLite pairing challenges and paired-device token hashes | Short-lived connections; one-time pairing consumption uses `BEGIN IMMEDIATE`. Exposes transaction-aware device-authentication methods so inference job state and revocation can serialize when they share the same DB. Uses `prepare_private_sqlite_path()`; WAL enabled. | Shares a database with inference jobs by explicit constructor check, but no application-wide quiescence or maintenance hook was found. |
| Inference jobs — `core/inference_jobs.py:InferenceJobService` | SQLite inference jobs; constructor requires the exact pairing database path | Short-lived connections; claims, lease expiry/reclaim and state transitions use transactions; device authentication is performed inside the same transaction for claim paths. Uses `prepare_private_sqlite_path()`; WAL enabled. | Pairing and inference form one database consistency unit, not two independently snapshot-able files. No global gate found. |
| Income opportunities — `core/income_opportunities.py:SQLiteIncomeOpportunityStore` | SQLite owner-scoped research pipeline | Short-lived connections; explicit transaction handling and `BEGIN IMMEDIATE` for capacity/state changes; WAL enabled. Uses `prepare_private_sqlite_path()`. | No global gate, cross-store backup manifest, or migration participation found. |
| OAuth connection metadata — `core/oauth_connection_registry.py:SQLiteOAuthConnectionRegistry` | SQLite non-secret owner/provider/account metadata; tokens are intentionally excluded | Short-lived connections and context-managed commit/rollback. WAL enabled. Creates parent directories directly and does **not** call `prepare_private_sqlite_path()` in the inspected implementation. | Important inconsistency: its path-hardening contract differs from the other inspected stores. No maintenance hook found. |
| Approval / MCP audit — `core/approval_audit.py:SQLiteApprovalAuditLog` | SQLite append-only metadata audit chain | Uses short-lived connections, explicit `BEGIN IMMEDIATE`, hash chaining, and a schema/data migration path. Uses `prepare_private_sqlite_path()`. | Hash chaining is not a backup or independent-witness guarantee. No global gate or coordinated snapshot hook found. |
| MCP policy — `core/mcp_tool_policy_store.py:SQLiteMCPToolPolicyStore` | SQLite owner/provider/account-scoped policy and monotonic revisions | Short-lived connections, WAL, busy timeout, and atomic revision/policy mutation semantics. Uses `prepare_private_sqlite_path()`. | Policy revision fencing is local to the store; no app-wide maintenance integration found. |
| Durable local task engine — `core/durable_task_engine.py:DurableTaskEngine` | SQLite goals, tasks, dependencies and append-only run events | Per-operation connections with explicit transactions. Has `close()` for engine-owned resources and an anchor connection for shared in-memory SQLite. File-backed path creates parent directories directly in inspected code; no `prepare_private_sqlite_path()` call was present. Documentation explicitly says SQLite mode is not a distributed database. | Legacy local task engine is distinct from the PostgreSQL workflow/goal runtime. No global maintenance hook found. |
| Conversation store — `core/conversation_store.py` / factory | SQLite by default under configured workspace, or PostgreSQL when configured | SQLite short-lived connections/WAL; PostgreSQL implementation owns a bounded `ConnectionPool` and exposes `close()`. Shared conversation runtime closes the conversation store only. | Previously audited: no app-wide writer gate; per-DB backup exists elsewhere but is not a multi-store consistency guarantee. |
| PostgreSQL conversation store — `core/postgres_conversation_store.py:PostgresConversationStore` | PostgreSQL conversation/message/generation tables | Owns a psycopg connection pool; startup initializes schema; explicit pool close. Requires backend-specific maintenance/drain semantics, not filesystem locking. | No shared coordinator contract found in inspected composition. |
| Workflow/task journal — `automation/workflow_store.py` / `automation/task_runtime.py` | SQLite workflow definitions, run state, approval metadata, leases and task event history in one database | Per-operation connections; WAL and `synchronous=FULL`; CAS/version checks and transactional event writes. No store-wide close/drain interface. Worker execution is a separate finite-cycle host responsibility. | SQLite consistency group; no global maintenance gate. See `docs/WORKER_AND_OUTBOX_LIFECYCLE_AUDIT.md`. |

## Cross-cutting observations

1. **Path-hardening is inconsistent.** Most inspected SQLite services call `prepare_private_sqlite_path()`, but the OAuth registry and legacy durable task engine do not in the inspected implementation. The helper itself still has the previously documented parent-directory/symlink and Windows ACL limitations; calling it is not proof that the whole path is private.
2. **Database grouping matters.** Pairing and inference jobs intentionally share one SQLite database so device revocation and job claims can serialize. A backup inventory must treat that as one physical database and preserve all tables together.
3. **Short-lived connections do not imply quiescence.** They reduce long-lived resource ownership but any other process can open a new connection unless a shared cross-process gate is enforced at every writer boundary.
4. **A process-local mutex is insufficient.** CLI, desktop, API, supervised workers, background processes and multiple application instances can overlap. Maintenance ownership needs durable cross-process fencing and stale-owner recovery.
5. **SQLite and PostgreSQL need different adapters.** SQLite requires coordinated writer drain and WAL-aware online backup/checkpoint policy. PostgreSQL requires pool/transaction drain and database-level snapshot/backup semantics. One generic file lock cannot cover both.
6. **Audit data needs special handling.** A local hash chain detects inconsistent history but does not itself provide independent event authenticity or a coordinated backup. Preserve audit stores and their external checkpoint/evidence dependencies as a consistency group.
7. **No startup defaults or owner data were changed.** This audit is read-only source inspection. No database was opened, copied, migrated, or modified; no tests were run locally.

## Required next inventory pass

Before designing or implementing the coordinator, continue tracing:

- every constructor and composition root for authentication, device pairing, inference, OAuth, MCP policy, approvals/audit, income, memory, evaluation, workflow, goals, outbox, security monitor, alert delivery, migration ledger and model/benchmark metadata;
- every background worker, scheduler, reconciliation loop, CLI command and desktop/API shutdown path that can write to those stores;
- file-backed JSON/YAML/artifact registries and object-storage references, not only SQL databases;
- which stores share a physical database, which are separate consistency domains, and which are externally hosted;
- current backup participation, restore ordering, schema versions, retention and data-classification requirements.

For each remaining store, record its owner, physical persistence, constructor path, write entry points, process multiplicity, close/drain behavior, backup mechanism, consistency dependencies and current maintenance integration.

## Design gate

Only after that inventory is complete should Yuki define a shared application-storage lifecycle contract. Normal writes should acquire a shared operation lease; maintenance should acquire an exclusive, durable, cross-process lease with a monotonic generation/fencing token, block new writes, drain active transactions, and hold ownership through snapshot and validation. Every writer must enforce it—not just the UI or backup command. A stale owner must be unable to resume after lease expiry.

The first implementation should be an opt-in maintenance coordinator with disposable multi-process tests, not an automatic migration. It must prove that an active writer blocks maintenance, a maintenance owner blocks new writers, process death is recoverable, stale generations are rejected, and both SQLite and PostgreSQL adapters drain safely. Only then should backup/restore be wired to it. Profile migration/cutover remains a later, separately approved milestone.

**Readiness decision:** inventory is partial. No global maintenance gate exists, and no claim is made that all persistent stores are protected. Keep existing backups and migrations explicit operator workflows; do not activate automatic storage migration or cutover.
