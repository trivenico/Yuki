# Yuki Canonical Storage Manifest — Draft

**Status:** design inventory only; not a runtime configuration or backup instruction  
**Review date:** 2026-11-26  
**Branch:** `research/reference-projects-integration-assessment`  
**Source audits:** `PERSISTENT_STORE_INVENTORY_AUDIT.md`, `PERSISTENT_STORE_INVENTORY_SUPPLEMENT.md`, `WORKER_AND_OUTBOX_LIFECYCLE_AUDIT.md`, `HOST_SCHEDULER_AND_WORKER_LIFECYCLE_AUDIT.md`, `CLI_DESKTOP_AND_HOST_WORKER_OWNERSHIP_AUDIT.md`

## Purpose and safety boundary

This draft converts the source audits into a common inventory shape. It deliberately marks unknown runtime owners, backup adapters, paths and restore order as `UNVERIFIED`; it must not be consumed by backup, migration, deployment or maintenance code. No data files were inspected to populate it. Paths below are implementation defaults/roles, not verified deployed locations.

The inventory is partial. New stores and writer processes may still be missing. A store is not “protected” merely because it appears here.

## Required manifest schema

Each store record should eventually provide:

| Field | Meaning |
|---|---|
| `store_id` | Stable machine-readable identifier; never derive from a mutable path |
| `backend` | SQLite, PostgreSQL, JSON file, object storage, or external service |
| `locator_ref` | Secret-free config key / logical locator; never put credentials or raw secrets here |
| `owner` | Composition root/process that owns lifecycle and closes the resource |
| `data_classification` | Public, internal, personal, sensitive, credential, audit, or mixed |
| `schema_version` | Schema or format version and migration authority |
| `key_dependency_ids` | IDs of external key dependencies, never key values |
| `writer_processes` | All processes/commands/services that can mutate the store |
| `consistency_group` | Stores that must be snapshotted/restored as one logical unit |
| `backup_adapter` | Reviewed backup implementation and version |
| `restore_order` | Dependency-aware restore sequence; unknown until proven |
| `retention_policy` | Owner-approved retention and deletion rule |
| `replay_policy` | Whether pending tasks/events/outbox rows may be replayed after restore |
| `maintenance_gate` | Exact shared/exclusive lease enforcement point; `NONE` until implemented |
| `evidence` | Source file/test references supporting each populated field |
| `status` | `PARTIAL`, `UNVERIFIED`, or `VERIFIED`; verification requires tests and deployment evidence |

## Initial register

| Store ID | Backend / implementation | Data classification | Writers / owner | Consistency and key dependencies | Backup / restore / replay | Status |
|---|---|---|---|---|---|---|
| `auth.accounts_sessions` | SQLite — `core/auth.py:BuiltInAuthService` | Sensitive identity/session metadata | API composition; desktop/CLI bootstrap paths exist. Full writer list and owning process: UNVERIFIED | Own DB. Password/session hashing parameters and schema version must be retained; no external encryption key dependency established by source audit | Backup adapter, restore order and session-revocation policy: UNVERIFIED | PARTIAL |
| `device.inference_jobs` | SQLite — `DevicePairingService` + `InferenceJobService` | Sensitive device credentials and job metadata | API/runtime injection; worker processes may claim jobs. Exact deployment owners: UNVERIFIED | One physical DB / one consistency group. Pairing and inference tables must be backed up together | Backup adapter and restore order: UNVERIFIED. Reconcile active leases and device revocation after restore; never replay stale claims blindly | PARTIAL |
| `income.opportunities` | SQLite — `SQLiteIncomeOpportunityStore` | Personal/business research data | API injection and any service caller: UNVERIFIED | Separate DB; schema migration authority needs explicit owner | Backup/restore/retention: UNVERIFIED | PARTIAL |
| `oauth.connection_metadata` | SQLite — `SQLiteOAuthConnectionRegistry` | Sensitive account/provider linkage metadata (not token secrets) | API runtime injection; caller lifecycle unresolved | Separate from credential vault; references may need consistency with vault records | Backup adapter/restore ordering with credential vault: UNVERIFIED | PARTIAL |
| `oauth.credential_vault` | SQLite — `SQLiteOAuthCredentialVault` | Credential ciphertext and token fingerprints | OAuth services; API/runtime caller ownership unresolved | Depends on externally managed AEAD and HMAC keys. Record key IDs/versions and recovery authority, never raw keys | DB-only restore is insufficient without matching key recovery. Restore ordering and replay policy: UNVERIFIED | PARTIAL |
| `audit.approvals` | SQLite — `SQLiteApprovalAuditLog` | Security/audit | Approval and authorization services; complete writer list unresolved | Append-only/hash-chain semantics; external checkpoint/witness dependency not proven | Must preserve chain and any independent checkpoint evidence; backup/restore policy UNVERIFIED | PARTIAL |
| `policy.mcp` | SQLite — `SQLiteMCPToolPolicyStore` | Sensitive authorization policy | MCP runtime/API services; caller lifecycle unresolved | Monotonic policy revisions; audit linkage should be retained | Backup/restore and rollback semantics UNVERIFIED; policy rollback requires explicit security review | PARTIAL |
| `tasks.legacy` | SQLite — `DurableTaskEngine` | Personal task payloads and execution metadata | Durable skill worker class exists; repository host owner not found | Separate legacy task journal; side-effecting tasks require no-blind-replay semantics | Backup/restore adapter and replay/reconciliation policy UNVERIFIED | PARTIAL |
| `tasks.workflow` | SQLite — `WorkflowRunStore` / `TaskRuntime` | Personal task payloads, approvals, leases and events | Workflow/specialist worker classes exist; no repository-owned scheduler identified | Workflow definitions, runs, leases, approvals and task events share one DB. AgentTaskJournal writes through TaskRuntime, not a separate store | Backup adapter UNVERIFIED. Reconcile leases and external side effects; no automatic replay of ambiguous actions | PARTIAL |
| `conversation.sqlite` | SQLite — `ConversationStore` | Personal conversations | CLI persistent chat; desktop composition; API composition | Conversation/messages/generation leases in one DB; workspace-based default currently couples data path to workspace | Desktop encrypted backup/migration primitives exist but no global writer quiescence; coordinated cutover/restore order UNVERIFIED | PARTIAL |
| `conversation.postgres` | PostgreSQL — `PostgresConversationStore` | Personal conversations | API/desktop caller depends on configured backend; full deployment owner unresolved | PostgreSQL pool is owned by conversation store. Requires DB-native snapshot and pool/transaction drain, not filesystem lock | Backup adapter/restore order UNVERIFIED | PARTIAL |
| `memory.companion` | JSON — `MemoryStore` / `core/vector_memory.py` | Personal memory | Companion application callers; exact CLI/desktop/API persistence paths unresolved | Per-file cross-process lock + atomic replace; not encrypted. Parent private-root assumption | File backup adapter/retention/restore validation UNVERIFIED | PARTIAL |
| `registry.model` | JSON — `providers/model_registry.py` | Internal model metadata | Provider/model registry callers; process owners unresolved | Save path lacks shared file lock in inspected implementation; concurrent updates may be lost | Backup/restore UNVERIFIED; fix locking before treating as multi-process safe | PARTIAL |
| `evaluation.runs` | SQLite — `EvaluationRunStore` | Personal evaluation prompts/results | Evaluation service/API injection; host owner unresolved | Append-only reports/idempotency claims; service semaphore is process-local only | Backup/restore and retention UNVERIFIED | PARTIAL |
| `evaluation.benchmarks` | SQLite or in-memory — `BenchmarkRegistry` | Internal benchmark definitions | Evaluation composition; exact process owner unresolved | Persistent only when DB path configured; in-memory mode is not a backup target | Persistent mode backup UNVERIFIED; record deployment mode before backup | PARTIAL |
| `audit.recovery` | SQLite — `RecoveryAuditLog` | Security/recovery audit | Recovery service callers; writer process unresolved | Separate DB; process-local RLock; no common path helper observed | Backup/checkpoint/retention UNVERIFIED | PARTIAL |
| `alerts.proactive` | SQLite — `ProactiveAlertStore` | Personal alerts and notification payloads | Alert producers; dispatcher caller not found | Alert history and notification outbox in same DB. Delivery claim/settle exists; no first-party dispatcher established | Backup adapter UNVERIFIED. Never blindly replay outbox after restore; reconcile delivery with provider idempotency | PARTIAL |
| `reminders.queue` | JSON — `ReminderQueue` | Personal reminder text and schedule | No in-repository scheduler/delivery caller found | Per-file lock; `due()` marks delivered before caller sends, creating at-most-once loss window | Backup/restore UNVERIFIED; delivery semantics must be redesigned or explicitly documented | PARTIAL |
| `runtime.model_eval` | No independent persistence — `providers/model_evaluation.py` | N/A | Called by evaluation/model workflows | Not a store; results belong in evaluation/model registry records | Exclude as independent persistence; map actual output destination | VERIFIED_NOT_A_STORE |

## Explicitly unresolved deployment fields

- Exact configured file paths and PostgreSQL endpoints for each deployment.
- Which API/desktop/CLI processes are actually enabled in the private pilot.
- External systemd/container/operator jobs not represented in this repository.
- Complete writer call graph for each store, including scripts, maintenance commands and background services.
- Schema versions and migration owners for every store.
- Backup adapters, encryption, retention, restore order and recovery objectives.
- External key custody and rotation evidence for OAuth vault records.
- Independent audit checkpoint storage and verification.
- Whether evaluation benchmark registry is persistent or in-memory in the target deployment.
- Whether any reminder/outbox delivery service exists outside the checked-in tree.

## Promotion criteria

A record may move from `PARTIAL` to `VERIFIED` only when all of the following exist:

1. Every writer process and entrypoint is enumerated from source and deployment configuration.
2. The owning composition root starts, drains, fences and closes the store and all associated workers.
3. The store participates in a tested cross-process maintenance gate; stale lease generations are rejected at the write boundary.
4. Backup is backend-aware and validated (SQLite integrity + foreign keys + application invariants; PostgreSQL snapshot/restore checks; JSON atomicity and schema validation).
5. Restore order, key dependencies, retention and replay policy are tested using disposable data.
6. A deployment-specific manifest is generated from validated configuration and checked against the actual private host.

## Readiness decision

This is a documentation draft only. It is not approved for automation. No backup, restore, migration, cutover, worker startup or production change is authorized by this manifest. Keep unknown fields explicit and keep unattended workers/outbox delivery disabled until their deployment-owned lifecycle and fencing are implemented.
