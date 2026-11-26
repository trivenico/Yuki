# Maintenance fence implementation status

**Review date:** 2026-11-26
**Branch:** research/reference-projects-integration-assessment

## Implemented prototype

The branch now contains:

- `core/maintenance_fence.py`
- `tests/test_maintenance_fence.py`

The prototype provides a context-managed exclusive lease. It serializes participating threads with a per-path reentrant lock and participating processes with an OS lock on a persistent file beneath the validated private profile directory. POSIX uses nonblocking `flock(LOCK_EX)`; Windows uses a byte-range `msvcrt.locking` lock. Nested calls on the same thread/path reuse the outer OS lease. Lock timeout, unsafe file type, hard links, unexpected POSIX ownership/permissions, and unsupported platforms fail closed. Timeout validation rejects booleans, negative/out-of-range values, NaN, infinities, and arbitrarily large integers without leaking conversion exceptions. The lock file is retained after release; the kernel releases the lock when the descriptor closes or the process exits.

The implementation deliberately uses exclusive locking for all operations. It does not yet provide shared-reader concurrency, a lock upgrade path, owner metadata, or a distributed/network-filesystem lock guarantee. On platforms with `os.register_at_fork`, a child process closes inherited lock descriptors and resets inherited thread coordination state; a regression test covers acquiring the lock after the parent releases it. Timeout boundary and invalid-value tests are also included. Lock-file validation failures close the opened descriptor, including when the path disappears or changes during inode verification.

## Important limitations

- Fork handling is best-effort process hygiene, not a guarantee for arbitrary native extensions or unsupported process-creation mechanisms. Prefer spawn/exec for worker processes; do not fork a multithreaded Yuki process while database work is active.
- `BuiltInAuthService` and `WorkflowRunStore` now accept an optional injected `maintenance_fence` and acquire its lease before opening each SQLite connection. The lease remains held through schema initialization, journal setup, transaction commit/rollback, and connection close. Focused tests assert the injected lease is entered for initialization and database use.
- Desktop startup now uses `build_control_plane_stores()` for its existing `auth.sqlite3` and `tasks.sqlite3` paths, so its authentication and workflow connections share one fence. The existing task database path is preserved; no automatic database move or rename occurs.
- The installed CLI conversation runtime remains deliberately separate: it creates no auth/workflow store and exposes no task execution tools. Its owner identity and conversation storage are not yet integrated with this control-plane fence. Do not interpret CLI process access as authenticated task execution.
- Participation remains incomplete: other direct constructors, scheduler, recovery, specialist worker and any external writer may still bypass the fence. The fence does not yet establish application-wide quiescence.
- The two stores still use separate SQLite databases. A shared maintenance lock coordinates migration/quiescence only when every writer participates; it does not make auth revocation and workflow claim one atomic transaction.
- The private-profile validator currently fails closed on Windows, so the Windows lock adapter is not usable in production until a verified Windows DACL/private-profile adapter exists.
- POSIX advisory locking semantics depend on the underlying filesystem. Production deployment must use a supported local filesystem; network filesystems and container volume semantics require explicit verification.
- A lock cannot stop an already-running provider call or undo an external side effect. Worker draining and target-side authorization/fencing remain separate requirements.
- The implementation has not been executed in local tests or CI at the time of this status update.

## Next integration gates

1. Make all production auth/workflow construction require the canonical factory; remove or restrict bypass constructors at composition boundaries.
2. Define and enforce the canonical profile-root policy, and reject stores wired to incompatible lock authorities.
3. Audit all auth and workflow mutators, including nested helpers, event writes, retries, revocation and recovery; verify no direct SQLite connection bypasses `_connect()`.
4. Wire scheduler, recovery and specialist-worker composition to the same authority; keep provider/network work outside the lease. Keep CLI task execution disabled unless it receives the same authenticated service composition.
5. Add multiprocess tests for revocation-vs-claim, concurrent claims, process death, timeout, and migration exclusivity.
6. Implement Windows private-profile ACL validation before claiming Windows support.
7. Only then build an exclusive migration operation that rechecks inventories under the lease and uses SQLite's backup API.

The prototype is not migration authorization. Database consolidation and consequential specialist execution remain disabled until all writer paths participate and the unified auth/workflow transaction boundary is independently verified.


## Canonical control-plane store composition prototype

Added `core/control_plane_stores.py` with `build_control_plane_stores()`. It validates/creates one private profile root, constructs one `MaintenanceFence`, and injects that exact instance into both `BuiltInAuthService` and `WorkflowRunStore` before their constructors initialize SQLite. Database filenames are constrained to distinct simple `.sqlite3` names beneath the profile root; per-store options cannot override the database path or fence. The factory fails closed when private-profile validation is unavailable.

Added `tests/test_control_plane_stores.py` covering shared fence identity, private-root placement, actual auth/workflow operations through the shared lock, path traversal/separator/NUL rejection, database-name collision, and refusal to silently repair an insecure existing directory.

**This remains a composition primitive, not proof that the application uses it.** Existing CLI and desktop compositions have not been switched to this factory, and the current CLI conversation path does not use the auth/workflow pair as a unified control plane. Scheduler, recovery and specialist-worker wiring also remain outstanding. The two databases remain separate; this factory coordinates maintenance but does not make auth revocation and workflow claim one atomic transaction. No local test run or CI success is claimed.


## Desktop and CLI composition status

The desktop launcher now imports `build_control_plane_stores()` and uses it to construct the existing `auth.sqlite3` and `tasks.sqlite3` databases under its already-prepared desktop root. The same `MaintenanceFence` instance is injected into both stores. The workflow store is now explicitly included in desktop startup cleanup and normal lifecycle shutdown, so the newly shared SQLite resource is not left out of lifecycle ownership. The profile root is deliberately passed as the existing desktop root to preserve current database paths; moving it to the newer `private_profile_root()` location requires the separately reviewed migration plan and is not done here.

The CLI is intentionally not given a task runtime: `build_local_conversation_runtime()` builds the conversation-only runtime with a deny-all tool registry. It does not construct `BuiltInAuthService` or `WorkflowRunStore`. The CLI's workspace-scoped conversation database and local owner identity therefore remain outside this maintenance-fence integration. Keep that restriction until CLI owner authentication and a safe private data-root migration are implemented.


## Local specialist worker and recovery composition

The opt-in `build_local_worker_runtime()` path previously constructed `WorkflowRunStore` directly without a maintenance fence. This meant its supervisor's expired-lease recovery, task claims, and terminal writes could bypass migration quiescence even though the desktop launcher had adopted the canonical factory.

The local worker composition now validates its configured workspace as an owner-private profile directory before creating task state, constructs a `MaintenanceFence` rooted there, and injects it into `WorkflowRunStore`. The worker and `WorkerSupervisor` share the same `TaskRuntime`, so claim, recovery, renewal, and state transitions use the fenced store connection path. A permissive existing workspace is rejected without chmod repair. Tests assert the fence is attached and the insecure workspace remains unchanged.

This is still a **local worker composition boundary**, not system-wide enforcement. The caller-selected workspace must genuinely be private; do not point it at a shared project directory. Windows remains fail-closed because private-profile ACL verification is not implemented. Other direct store constructors, external writers, and the CLI conversation database remain outside this fence. The fence only coordinates participating operations; auth and workflow are still separate databases, so it does not solve live-session revocation versus claim atomicity or provide target-side fencing for in-flight effects.

## Additional task-persistence inventory: parallel stores outside this fence

A repository-wide source-tree review found several independent SQLite task/lifecycle implementations in addition to `WorkflowRunStore`:

| Implementation | Source | Persistence boundary | Maintenance-fence status |
|---|---|---|---|
| Durable specialist workflow queue | `automation/workflow_store.py` | Approval-aware workflow rows, claims, leases, events | Optional injected fence; adopted by desktop and the opt-in local worker composition |
| Durable task engine | `core/durable_task_engine.py` | Separate goals/tasks/dependencies/run-events schema and its own SQLite connections | No maintenance-fence injection or shared auth-session predicate |
| Task lifecycle store | `core/task_lifecycle.py` | Separate task lifecycle and hash-chained event tables | No maintenance-fence injection or shared auth-session predicate |
| Agent runtime task store | `yuki_agent_runtime.py` | Separate agent task queue, leases and task events | No maintenance-fence injection or shared auth-session predicate |
| Workspace task list | `yuki_tasks.py` | File-backed user task list | Not part of the SQLite control-plane fence |

These are distinct persistence models, not aliases for `WorkflowRunStore`. The presence of a maintenance fence on the specialist workflow store does not quiesce these other stores or their callers. This inventory is a source-level discovery, not proof that every implementation is currently reachable from the desktop/CLI production composition; each constructor and call graph still needs an explicit reachability and ownership audit.

### Required containment decision

Until each alternative task store is either retired, migrated behind the canonical control-plane services, or explicitly classified as non-consequential local data:

- Do not use it as an authorization source for privileged tools, browser actions, deployments, secrets, account changes, or other consequential effects.
- Do not assume the control-plane maintenance lock covers its database/file writes.
- Do not merge its data into the auth/workflow migration implicitly. Any consolidation needs a separate schema, provenance, idempotency, lease and recovery plan.
- Keep worker execution disabled for any queue whose claim path does not validate a live owner session or a narrowly scoped service identity at the protected transaction boundary.
- Add a call-graph inventory showing every constructor, composition root, CLI/API/desktop entrypoint, scheduler, worker, recovery path and external writer before claiming global quiescence.

The next engineering increment should classify actual runtime reachability for these stores, then either fence their complete transaction paths using the same canonical maintenance authority or document and enforce that they are disabled/non-consequential. Avoid adding another task-store abstraction until this disposition is complete.

## Coding-task persistence and execution evidence: additional uncovered stores

A focused follow-up inspection found two more SQLite persistence boundaries that were not in the initial parallel-task-store table:

| Implementation | Source | Stored state | Maintenance-fence / authorization status |
|---|---|---|---|
| Coding task plans and patch proposals | `core/coding_tasks.py` (`CodingTaskStore`) | Plans, workspace, patch text and digest, review state/version | Direct SQLite connections; no maintenance-fence injection or private-profile validation |
| Coding execution evidence | `core/coding_task_execution.py` (`CodingTaskExecutionStore`) | Per-run task/patch/command/output digests, exit status, byte count and timing | Separate direct SQLite connections; no maintenance-fence injection or shared transaction with the task record |

### Execution boundary observations

- `execute_approved_coding_task()` checks that the task is in `approved` state, verifies the patch digest, binds the requested workspace to the reviewed workspace, validates an executable allowlist, and delegates to the Docker sandbox runner.
- It requires a caller-supplied approval callback for every invocation and passes the exact task ID, patch digest, and sandbox request to that callback. The callback's actual identity/session binding is therefore a composition responsibility; this function alone does not prove that the callback is backed by a live owner session.
- The task's `approved` state is read before execution and is not atomically consumed or transitioned to a one-shot running state by this function. Repeated calls can reach the approval callback again while the record remains approved. Treat the callback as mandatory per-run authorization, not as a one-time approval implied by the stored state.
- Execution evidence is persisted after the sandbox run in a separate database transaction. A crash or evidence-store failure after command execution can leave an executed run without a durable evidence row. The sandbox runner's cleanup and approval controls do not make task-state and evidence writes atomic.
- Neither store participates in the canonical maintenance fence. A maintenance operation that locks only auth/workflow stores does not quiesce coding-task edits, approvals, sandbox launches, or evidence writes.

### Containment and required remediation

Until these stores are migrated or explicitly fenced and authorized:

1. Do not include coding-task databases in claims of application-wide maintenance quiescence or the auth/workflow migration.
2. Keep execution disabled in production compositions unless the approval callback is demonstrably backed by a live owner session, is bound to the exact request and current policy revision, and rechecks immediately before sandbox launch.
3. Add an atomic execution-claim/approval-consumption state transition with a unique run/idempotency identity; define recovery for crashes before launch, during execution, and after side effects but before evidence commit.
4. Persist task transition and execution evidence under a reviewed transaction/outbox design, or explicitly model the evidence gap as an uncertain outcome that blocks automatic replay.
5. Move both stores under a validated private profile and the same maintenance authority, or retire them behind the canonical control-plane service. Do not silently change existing database paths; provide a separately reviewed migration.
6. Add tests for concurrent duplicate execution, approval revocation immediately before launch, process crash at each execution/evidence boundary, and maintenance-lock exclusion.

The source review establishes the store and function behavior above; it does not establish that a production host currently exposes this coding-task execution path. Reachability through every CLI, desktop, API, and external host composition remains to be traced.

## Additional independent persistence domains: durable queues, conversations, OAuth and policy

A repository-tree follow-up found that the canonical auth/workflow fence is not a global persistence fence. The following independently constructed stores remain outside it:

| Store | Source | State / security significance | Current maintenance integration |
|---|---|---|---|
| Legacy durable agent queue | `core/durable_task_engine.py` (`DurableTaskEngine`) | Goals, queued/running tasks, payload/results, leases and run events; consumed by the durable-skill worker abstraction | Direct SQLite connections; no private-profile validator or maintenance-fence injection |
| Legacy task lifecycle journal | `core/task_lifecycle.py` (`SQLiteTaskLifecycleStore`) | Task state, payload/result and hash-chained lifecycle events | Direct SQLite connections; no private-profile validator or maintenance-fence injection |
| Conversation history | `core/conversation_store.py` / `core/conversation_store_factory.py` | Owner conversation records and metadata | SQLite path/file hardening is applied through `prepare_private_sqlite_path`, but the store is not attached to the canonical maintenance fence; factory roots SQLite at the configured workspace |
| MCP tool policy | `core/mcp_tool_policy_store.py` | Owner/provider/account-scoped tool permissions and monotonic policy revisions | Private SQLite path preparation, but independent connections and no canonical maintenance-fence injection |
| OAuth credential vault | `core/oauth_credential_vault.py` | Encrypted access/refresh-token ciphertext, key IDs, expiry, scopes and refresh-token digests | Private SQLite path preparation and application-layer AEAD, but independent connections and no canonical maintenance-fence injection |
| OAuth connection registry | `core/oauth_connection_registry.py` | Provider/account connection metadata | Creates parent directories directly; no canonical private-profile/fence integration established |
| Evaluation run store | `evaluation/run_store.py` | Evaluation run metadata/results | Private SQLite path preparation, but independent connections and no canonical maintenance-fence injection |
| Coding task stores | `core/coding_tasks.py`, `core/coding_task_execution.py` | Patch plans/approval and sandbox execution evidence | Direct SQLite connections; no canonical maintenance-fence injection |

### Runtime and security disposition

- `DurableTaskEngine` is a separate queue/state machine with atomic SQLite claims and lease methods. `DurableSkillWorker` accepts an engine and runtime by injection and invokes a fixed `skill.invoke` type; the inspected source/docs do not establish a production host composition or scheduler for it. Treat the queue as a dormant/externally hosted execution surface until the actual deployment owner is identified. Do not merge its database into the workflow DB by path aliasing or assume its lease semantics inherit workflow-store protections.
- `SQLiteTaskLifecycleStore` explicitly records lifecycle state and a hash chain but is a separate persistence implementation. A hash chain detects some edits; it does not provide authorization, cross-process maintenance exclusion, independent anchoring, or protection from a writer that can rewrite the whole database.
- Conversation storage already applies SQLite file/path hardening, but file permissions are not equivalent to maintenance coordination. The current SQLite factory derives `conversations.sqlite3` from `workspace`; this remains a separate data-root and migration concern. The PostgreSQL conversation adapter is another separately owned pool/transaction boundary.
- MCP policy revisions are security-sensitive: a maintenance/backup operation must preserve policy rows and revision counters together. Runtime policy freshness checks are not replaced by taking the auth/workflow maintenance lock.
- OAuth credential ciphertext is sensitive even when encrypted. The DB, encryption key provider, HMAC key and rotation lifecycle form one recovery boundary; backing up only the SQLite file is insufficient to guarantee decryptability or safe token rotation. Do not copy credentials into a combined control-plane DB without a separate key/rotation design.
- Evaluation records and reports may contain prompts, outputs, model metadata or private benchmark data depending on caller inputs. Keep evaluation/training data isolated from production conversation stores and never grant Kaggle or model-training jobs access to production credential stores.
- The current canonical factory is intentionally narrow: it coordinates only the auth and workflow SQLite stores injected into it. It is not a global application lock and must not be described as one.

### Next remediation sequence

1. Build a verified runtime reachability map for each store: constructor call sites, owning composition root, CLI/desktop/API exposure, worker entrypoints, and external deployment references. Do not infer “unused” solely because a first-party scheduler was not found.
2. Classify each store as (a) part of the unified control plane, (b) private owner data with a separately scoped backup/maintenance protocol, (c) isolated evaluation/training data, or (d) retired/dormant. Record the owner and shutdown/backup contract.
3. Extend the canonical maintenance authority only to stores that must be quiesced together. Inject the same fence before schema initialization and hold it through each complete SQLite transaction/connection lifecycle; avoid an indiscriminate global lock around unrelated evaluation or read-only stores.
4. For conversation, OAuth, MCP policy, and evaluation stores, define independent consistent backup/restore groups, including WAL/SHM handling, key dependencies, retention and restore validation.
5. For the legacy durable task queue and task lifecycle journal, decide whether to retire them in favor of the authenticated workflow runtime or maintain separate queues with explicit auth, approval, maintenance, worker ownership and recovery contracts. Never run two independent queues as if they share one approval or lease authority.
6. Do not move any existing database path, merge schemas, or alter production credential locations in this audit increment. Such changes require an explicit data migration plan, quiescence, backup/restore proof and rollback design.

This is a source-level inventory extension, not proof that every listed store is active in the current deployment. No production host scheduler or complete constructor reachability was established for every legacy queue. No database was opened or changed, no credentials were read, no local tests were run, and no deployment configuration was modified.
