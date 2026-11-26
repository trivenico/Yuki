# Persistent Store Inventory — Additional Domains

**Review date:** 2026-11-26  
**Branch:** `research/reference-projects-integration-assessment`  
**Companion audit:** `docs/PERSISTENT_STORE_INVENTORY_AUDIT.md`

This supplement records a second source-inspection pass. It extends, but does not replace, the first inventory. Findings are limited to the named implementations and do not establish that every repository persistence path has been found.

## Additional inspected persistence domains

| Domain | Implementation | Persistence / lifecycle | Maintenance and security observations |
|---|---|---|---|
| Long-term memory | `companion/memory.py:MemoryStore`, wrapped by `core/vector_memory.py` | JSON file. MemoryStore uses an in-process RLock plus `core.json_file_store.file_lock()` around read/modify/write; writes use atomic JSON replacement. | This is a useful cross-process per-file serialization primitive (POSIX flock / Windows byte-range lock), not a global maintenance lease. The helper assumes a private parent directory and does not encrypt data. Memory path is resolved in the constructor. |
| Shared JSON persistence primitives | `core/json_file_store.py:file_lock`, `atomic_write_json` | Adjacent lock file; POSIX flock or Windows msvcrt; atomic temp-file replacement, file fsync and POSIX directory fsync. | Lock helper calls `ensure_private_directory()`; documentation explicitly says it assumes the parent is private and provides no encryption. `atomic_write_json()` itself does not acquire the lock; callers must hold `file_lock()` across the full read/modify/write. Do not infer every JSON writer uses it. |
| Workflow run journal | `automation/workflow_store.py:WorkflowRunStore` | SQLite workflow definitions, run state, leases, approvals and task events. Per-operation connections. WAL and `synchronous=FULL`; explicit transactions and CAS/version semantics. Uses `prepare_private_sqlite_path()`. | Durable task/workflow journal is separate from the legacy `core.durable_task_engine.DurableTaskEngine`. No application-wide maintenance hook or global writer lease found. |
| Agent task checkpoint adapter | `automation/agent_task_journal.py:AgentTaskJournal` | Does not own a database; writes through injected `TaskRuntime` to workflow/task persistence. Requires active worker lease and CAS version. | Must be covered through the underlying TaskRuntime/store writer boundary; do not register it as a separate physical database. |
| Evaluation runs | `evaluation/run_store.py:EvaluationRunStore` | SQLite append-only owner-scoped reports and idempotency claims. WAL, `synchronous=FULL`, per-operation connection; revalidates final DB path before each open. Uses `prepare_private_sqlite_path()`. | Distinct persistent store. No global maintenance gate found. Evaluation service has an in-process bounded semaphore, which limits concurrency only within that service instance and is not a cross-process maintenance lock. |
| Benchmark definitions | `evaluation/benchmark_registry.py:BenchmarkRegistry` | Immutable content-addressed definitions; optionally SQLite-backed with WAL and `synchronous=FULL`, or in-memory when no DB path is configured. Has a process-local RLock. | Must distinguish persistent deployments from in-memory configuration. RLock does not coordinate other processes; no global maintenance hook found. |
| Model evaluation helper | `providers/model_evaluation.py` | Bounded remote inference helper; does not itself persist results. | Not a persistent store. Its outputs may be persisted by the separate model registry or EvaluationRunStore; do not count it as a database. |
| Local model registry | `providers/model_registry.py` | JSON file with bounded records and atomic temp-file replacement. | Does not use the shared `file_lock()` in the inspected save path; concurrent read/modify/write callers can race and lose updates. Parent is created directly; no private-directory validator or explicit POSIX mode/ACL verification is visible in this implementation. No cross-process maintenance hook found. |
| Recovery audit | `core/recovery_audit.py:RecoveryAuditLog` | SQLite metadata log; short-lived connections; RLock; bounded retention deletes older events. | Parent directory is created directly; no `prepare_private_sqlite_path()` call in the inspected implementation. RLock is process-local. No WAL/backup/maintenance contract was found in the inspected class. |
| Proactive alert history and notification outbox | `core/proactive_alerts.py:ProactiveAlertStore` | SQLite alert history plus pending/claimed/delivered/failed notification outbox; transactions use `BEGIN IMMEDIATE`; short-lived connections. | Atomic SQL operations can coordinate concurrent SQLite writers, but this is not a global maintenance barrier. Parent is created directly; final file is chmod'ed to 0600 on a best-effort basis with errors ignored. The inspected implementation does not use the shared private SQLite path helper. Delivery worker lifecycle and shutdown must be traced separately. |
| OAuth credential vault | `core/oauth_credential_vault.py:SQLiteOAuthCredentialVault` | SQLite metadata plus host-provided AEAD ciphertext; HMAC refresh-token fingerprints; short-lived connections and WAL. Uses `prepare_private_sqlite_path()`. | This is a distinct sensitive database from OAuth connection metadata. Backup/restore must preserve key identifiers and the externally managed AEAD/HMAC key lifecycle; a database-only restore without compatible key material may make credentials undecryptable. No global maintenance hook found. |

## Additional architecture findings

### 1. There are multiple independent task/evaluation journals

At least three distinct durable task/evaluation state families are present in the inspected source:

- legacy local task engine: `core/durable_task_engine.py`;
- workflow run journal and agent checkpoints: `automation/workflow_store.py` plus `automation/agent_task_journal.py`;
- evaluation run journal and optional benchmark registry: `evaluation/run_store.py` and `evaluation/benchmark_registry.py`.

They must not be conflated into one logical “task database”. The task checkpoint adapter writes through its injected runtime and should be covered at the underlying store boundary.

### 2. Per-file locks are useful but have narrow scope

The shared JSON file-lock helper demonstrates that Yuki already has a portable per-file cross-process lock abstraction. It does not solve:
- exclusive maintenance across multiple files and databases;
- writer drain across independent application processes;
- monotonic durable generations or stale-owner fencing;
- coordinated snapshots across SQLite and PostgreSQL;
- consistent inclusion of related files, sidecars, keys and external services.

The model registry's JSON save path should be reviewed for concurrent updates and aligned with the shared locking/atomic-write convention before it is treated as a reliable multi-process registry.

### 3. Storage path policy is not yet uniform

The additional pass found several classes that create parent directories directly or use best-effort chmod rather than the shared private SQLite path helper / private-profile validator. These are concrete follow-up targets, not proof that their data is currently exposed: actual risk depends on configured paths, OS account permissions and deployment. Nevertheless, production hardening should converge on one validated private-root and no-follow open contract, with real Windows DACL verification rather than assuming profile defaults.

### 4. Backups must include key dependencies and outbox state

A coherent backup inventory cannot be limited to database files. It must capture:
- encrypted OAuth vault database plus recoverable, versioned key custody/rotation metadata (never raw keys in the backup manifest);
- workflow/task state and pending outbox records with their leases and idempotency keys;
- memory and model-registry JSON files plus their lock/atomic-write conventions;
- audit and recovery records, with independent checkpoint evidence where required;
- benchmark definitions and evaluation records needed to reproduce a report.

Do not restore credential ciphertext unless the matching key identifiers and key recovery path are available and verified. Do not blindly replay notification outbox rows after restore; delivery needs an explicit reconciliation policy to avoid duplicate external side effects.

## Revised next steps

1. Finish the repository-wide inventory for all file and database writers, including the full PostgreSQL workflow repository, worker/outbox/reconciler lifecycle, security/alert services, and desktop/API/CLI composition.
2. Build a canonical storage manifest schema: store ID, backend, path/endpoint reference, owner, data classification, schema version, key dependency IDs, writer processes, backup adapter, restore order and consistency group.
3. Close the identified path/locking inconsistencies as separate, narrowly scoped hardening changes. In particular, model-registry JSON read/modify/write must serialize across processes; SQLite stores that bypass the shared path helper need a reviewed reason or a common validator.
4. Design a coordinator with a shared/exclusive lease and monotonic fencing generation. It must cover SQL transaction entry points, JSON/file mutation entry points, worker claims/outbox delivery, and external PostgreSQL pools.
5. Prove coordinator behavior with multi-process and crash-injection tests before wiring backup/restore. A maintenance lock must stop new side effects as well as database writes; a queued notification or workflow executor can otherwise emit an external action during a supposedly quiescent backup.

**Readiness:** still an incomplete inventory. No code/runtime changes were made by this supplement. No database was opened or modified, no local tests were run, and no backup, restore, migration or cutover was executed.
