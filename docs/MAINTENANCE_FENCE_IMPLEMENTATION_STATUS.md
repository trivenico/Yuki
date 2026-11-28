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

## Runtime reachability trace: API-owned OAuth and MCP surfaces

A targeted composition-root trace now distinguishes independently stored components that are actually exposed by the authenticated ASGI builder from legacy stores whose host invocation remains unproven.

### Confirmed API-reachable paths

1. **OAuth connection registry**
   - `build_conversation_runtime()` accepts an injected `SQLiteOAuthConnectionRegistry` and wraps the application with authenticated OAuth connection and authorization APIs.
   - The connection API reads owner-scoped connection records and removes a record after the disconnect service reports success.
   - The authorization API registers a completed provider grant in the registry; if registration fails, it invokes the provider service's compensating `discard_unregistered_grant` path when available.
   - This is a confirmed API-reachable persistence boundary when the optional OAuth dependencies are supplied by the host. The registry remains caller-owned: `ConversationRuntime.close()` closes only the conversation application, not injected OAuth services or registries.

2. **MCP tool policy store and audit log**
   - `MCPRuntimeService` requires an injected `SQLiteMCPToolPolicyStore` and `SQLiteApprovalAuditLog`, plus trusted host-configured provider managers/configuration.
   - The authenticated API builder exposes MCP management when both the MCP runtime and OAuth connection registry are supplied.
   - Owner-authenticated API operations can list policy state, mutate enabled policy using expected revisions and explicit owner confirmation, discover tools, and create policy-bound clients subject to the service's trusted definitions, OAuth scopes and approval callback.
   - The policy store and audit log are injected dependencies; the ASGI runtime does not construct, fence, or close them. Policy and audit databases therefore remain separate maintenance/backup groups unless the host composition explicitly coordinates them.

3. **OAuth credential vault**
   - `build_oauth_credential_vault()` constructs the encrypted SQLite vault using an OS keyring provider and refuses implicit key bootstrap.
   - OAuth authorization/disconnect services are supplied to the API through host-owned provider resolver callbacks. The builder does not itself construct the complete provider service graph or prove that every host uses this vault.
   - When a host wires the vault into those callbacks, it is reachable from authenticated OAuth authorization/disconnect operations, but its database and OS keyring material remain outside the API runtime's close/fence ownership. A database-only backup is not a complete credential recovery set.

### Lifecycle ownership finding

`ConversationRuntime` explicitly closes only `ConversationApplicationRuntime`. The API builder accepts auth, OAuth registry, OAuth service resolvers, MCP runtime, evaluation service, income store, pairing and inference services as injected dependencies. They are not implicitly owned or closed by the returned runtime. This is a deliberate caller-ownership contract in the current code, but it means a deployment needs a higher-level host composition that owns and drains every injected resource.

Do not solve this by making the ASGI runtime blindly close every injected object: shared instances may be owned by another service, and the current interfaces do not all expose uniform idempotent close/drain methods. First define explicit ownership transfer or a resource registry with lifecycle adapters and dependency order.

### Legacy stores: reachability remains unproven

The repository source review did not establish production host wiring for `DurableTaskEngine`, `SQLiteTaskLifecycleStore`, `ReminderQueue`, or `ProactiveAlertStore` delivery. Their classes and worker APIs exist, but no verified in-repository composition path in this pass proves they are started in the deployed host. Mark them `implemented / host-owner-unverified`, not `production-active` and not `unused`. External scripts, operator commands and deployment manifests still require inspection.

### Concrete next implementation boundary

Before adding more maintenance-fence injections, introduce a typed host resource manifest that records, for each constructed resource: stable resource ID, data path/backend, sensitivity class, owner, lifecycle adapter, backup consistency group, key dependencies, and whether it participates in the maintenance fence. The manifest must be generated from the actual composition, not a hand-maintained list that can drift. Startup should fail closed when a required persistent resource has no declared owner or backup class. Shutdown should stop accepting work, stop claims, drain in-flight operations, close resources in reverse dependency order, and report any incomplete drain; it must not claim that a timeout forcibly stopped an external provider call.

This section records source-level reachability through optional API composition, not evidence that those optional services are configured in a deployed production instance. No service was started, no OAuth token or key material was accessed, no database was modified, and no local tests were run.

## Default web launcher: private bind hardening and remaining composition gap

The source-level host trace reached `scripts/run_web.py`, the repository's default Uvicorn launcher. Before this increment it allowed a non-loopback bind when `YUKI_WEB_ALLOW_PUBLIC_BIND=true` was set. That escape hatch conflicted with Yuki's owner-only deployment boundary: an environment flag is not a private-network authorization or proof of a trusted TLS proxy.

The launcher now accepts only `127.0.0.1`, `localhost`, or `::1`; rejects wildcard, LAN, public-IP, and hostname listeners; no longer recognizes `YUKI_WEB_ALLOW_PUBLIC_BIND`; and documents that remote owner access must terminate at a separately secured same-host reverse proxy exposed only on the owner's private network.

A focused `tests/test_run_web.py` covers accepted loopback values and rejected wildcard/non-loopback/non-string values. This is a source change and an authored test only; no local test execution is claimed.

### Remaining default-web host gaps (not silently treated as fixed)

`scripts/run_web.py:create_app()` still constructs `BuiltInAuthService` directly at `workspace/auth.sqlite3`, rather than using `build_control_plane_stores()`. It also creates inference, income and provider-selection state under the workspace. The web launcher does not currently wire the optional OAuth registry, OAuth vault, or MCP management services.

Do not switch the auth database to `private_profile_root()` automatically: that changes the existing database location and requires a separately reviewed, quiesced migration with backup and rollback. Do not point the canonical factory at an arbitrary workspace either: the private-profile validator must reject permissive/shared roots rather than silently chmod them.

The next safe implementation step is a web-host composition migration plan that inventories the existing workspace databases, validates a canonical owner-private destination, and requires explicit operator approval before any copy/cutover. Until then, the web auth store remains outside the shared maintenance fence, and the loopback-only bind change must not be mistaken for complete host hardening.

## Default web-host data-root migration preflight

Added `core/web_host_migration.py` and focused tests. The read-only planner inventories the stock `scripts/run_web.py` persistence footprint and maps it to a proposed private-profile destination without changing the launcher or moving data:

- `auth.sqlite3`: auth users and sessions, including required schema validation.
- `inference.sqlite3`: device-pairing challenges, paired devices and inference jobs. These services deliberately share one database and remain one consistency unit.
- `income-opportunities.sqlite3`: owner-scoped opportunity pipeline.
- `conversations.sqlite3`: included for the SQLite conversation backend; omitted for PostgreSQL, whose backup/migration is external.
- `provider-selection.json`: source/destination metadata and collision checks only; the JSON contents are not read or included in the report.

SQLite inventories reuse the existing read-only preflight, recording schema objects/fingerprint, aggregate table counts, integrity, foreign-key status and WAL/SHM/journal sidecar metadata without returning application row values. The planner checks destination collisions and unsafe parent paths, reports consistency groups and always returns `dry_run=True`, `executable=False`. It does not create the profile directory, copy files, run a backup, or modify configuration.

Migration remains blocked pending a separately reviewed executor: quiesce the web host and all writers, validate the destination with the private-profile validator, use SQLite's backup API for each database (including WAL state), preserve the inference/pairing database as one unit, atomically persist provider selection with owner-only permissions, verify integrity/schema/count continuity, and retain source backups for a tested rollback. PostgreSQL conversation storage requires its own provider-specific backup plan. The default web launcher still constructs its auth and other stores under the workspace; this preflight does not silently change those paths or claim that the web host now participates in the shared maintenance fence. No local tests or CI success are claimed.

## Web-host runtime resource ownership and shutdown

Added `core/runtime_resources.py` with an explicit `RuntimeResourceGroup`: host-transferred resources are deduplicated by identity, closed in reverse construction order, shutdown is idempotent, and cleanup continues after an individual close failure. `ConversationRuntime` now accepts this group, closes its conversation application first, then closes transferred host resources. Composition failures after ownership transfer close both the partially built conversation application and the group.

The default `scripts/run_web.py` now registers the resources it constructs (auth service, provider manager, device pairing, inference jobs, and income store) and transfers ownership to `ConversationRuntime`. If startup fails partway through construction, the launcher closes the resources already registered. The provider manager now owns active and replaced provider instances, closes them on shutdown, and closes a newly constructed candidate if persisting a selection fails. This matters for providers such as `HybridProvider`, which can own child providers.

Most SQLite service objects in this launcher open and close a connection per operation, so they do not currently expose a persistent `close()` method; the resource group records ownership but has no persistent connection to release for those objects. The conversation store is closed by `ConversationApplicationRuntime`. The provider manager is the currently closeable long-lived provider owner.

Tests added for reverse-order/idempotent cleanup, continuing after close errors, cleanup after failed ASGI composition, runtime shutdown ordering, and active/retired provider closure. These tests are authored only; they have not been run locally. This does not solve writer quiescence, shared maintenance-fence adoption, private data-root migration, ASGI in-flight request draining, or process termination of hung provider calls. Uvicorn shutdown behavior and real provider teardown still require integration testing. No migration, merge, or deployment occurred.

## Web-host shared maintenance-fence adoption

The stock loopback-only web launcher now constructs one `MaintenanceFence` rooted at the canonical `private_profile_root()` and injects that same fence into its persistent SQLite services:

- `BuiltInAuthService` (existing workspace `auth.sqlite3` path is preserved);
- `ConversationStore` through the shared conversation application/runtime factory;
- `DevicePairingService` and `InferenceJobService` (the existing shared `inference.sqlite3` remains one consistency unit);
- `SQLiteIncomeOpportunityStore`.

Each participating store acquires the fence before opening SQLite and retains it through connection cleanup. The inference/pairing services share the same fence for their common database. The host continues to own resource shutdown through `RuntimeResourceGroup`; the fence itself is a coordination primitive, not a data migration.

A focused test module verifies that schema initialization acquires the injected fence across these stores, and the API runtime test verifies the fence is propagated to conversation persistence. The web launcher does not move or rename any database, and no data migration is performed.

### Boundaries and remaining risks

- This is cooperative coordination: any other process or legacy entrypoint opening one of these database files without the same canonical fence remains outside the lock protocol.
- The workspace database parent directories and their existing permissions are not repaired or migrated by this change. Existing SQLite file hardening remains in force, but a shared lock does not make an unsafe workspace private.
- The current fence serializes all participating reads and writes exclusively. This is intentionally conservative for the private MVP and may become a throughput bottleneck; do not weaken it to shared locks without a separate concurrency design and tests.
- PostgreSQL conversation storage is not covered by this SQLite fence.
- This does not implement ASGI request draining, in-flight provider cancellation, or a host-wide shutdown barrier. Maintenance must not begin while uncoordinated requests or external workers can still mutate state.
- Desktop and web deployments must resolve to the same validated canonical private profile root to coordinate with one another. The web host must fail closed if that root cannot be validated.
- No local tests were run. CI results must be checked on the final PR head before treating this integration as verified.

## ASGI request draining and shutdown barrier

`ConversationRuntime` now tracks in-flight HTTP requests around the complete ASGI application call. The ASGI lifespan shutdown path first marks the runtime as draining, rejects newly arriving HTTP requests with a bounded plain-text 503 response and `Retry-After`, then waits for the active request count to reach zero before closing the conversation application and transferred resources.

The drain timeout is configurable through `build_conversation_runtime(..., shutdown_timeout_seconds=30.0)`, validated as a finite numeric value between zero and one hour. If the deadline expires, the lifespan emits `lifespan.shutdown.failed` with the active-request count and deliberately leaves resources open; it does not claim to cancel or forcibly terminate those requests. Direct synchronous `close()` also refuses to close while the active request count is nonzero, requiring the caller to drain first.

Tests were added for the successful drain path, rejection of requests arriving after drain begins, direct-close refusal while a request is active, timeout reporting without premature resource closure, and invalid timeout values.

### Limits

- The drain tracks HTTP ASGI calls entering this `ConversationRuntime`; it does not track arbitrary background tasks, scheduler threads, external workers, provider work detached from the request, or processes using the same databases.
- A request that hangs past the deadline remains active. The host must surface the failed lifespan shutdown and an operator/supervisor must decide whether to wait, investigate, or terminate the process. Terminating a process is not equivalent to a graceful cancellation and may leave external side effects requiring reconciliation.
- The web launcher still relies on Uvicorn's server-level connection/shutdown behavior. End-to-end behavior under keep-alive, streaming responses, disconnects, cancellation, and provider stalls needs an integration test with the pinned Uvicorn versions.
- This is not yet a global maintenance barrier: every background writer/worker must separately stop claiming work and participate in the same maintenance protocol before a consistent migration or backup may begin.
- Tests are authored only; no local test execution or CI success is claimed.

## Opt-in worker supervisor drain

- Confirmed the stock `scripts/run_web.py` composition does not instantiate `LocalWorkerRuntime`, `WorkerSupervisor`, a scheduler, or an automatic specialist worker. The local worker factory is an explicit opt-in composition and documents that it creates no threads or automatic work.
- Added a bounded shutdown lifecycle to `WorkerSupervisor`: new cycles are rejected after draining begins; the supervisor signals its worker pool to stop taking further claims; the stop check and SQLite claim are serialized against the drain transition by the supervisor condition; and shutdown waits for all active cycles to unwind.
- `SpecialistTaskWorker.run_once()` and `run_concurrently()` accept an optional stop event and claim guard. Standalone use remains compatible; supervisor-managed execution receives both controls.
- A shutdown timeout returns `False` and leaves dependencies untouched. It does not kill or interrupt an in-flight synchronous provider call. The caller must keep the provider and task store alive until a later successful drain; a provider that never returns can therefore prevent graceful shutdown.
- Added regression coverage for a blocked handler, stop-before-next-claim, timeout, successful later drain, rejection of new cycles, and invalid timeout values. Tests are authored only; no local test execution or CI success is claimed.
- This lifecycle applies only to cycles explicitly run through this `WorkerSupervisor` instance. It does not coordinate other supervisor instances/processes, legacy `DurableTaskEngine` workers, schedulers, desktop QThreadPool jobs, detached tasks, or target-side external effects. Those remain separate lifecycle and authorization boundaries.
- The worker supervisor's in-process claim guard is not a distributed fence. Production worker hosting still needs a single explicit owner for supervisor lifecycle, provider/store ownership, process-level termination policy, durable worker identity, and external-effect fencing.

## Bounded desktop logout and chat-operation drain
- The desktop composition uses one Qt `QThreadPool` worker thread for provider/chat/plan/backup/logout/shutdown work. Its normal window-close path queues `ShutdownWorker` behind preceding pool work, so resource closure is ordered after those queued operations finish. This is an application-level ordering mechanism, not process isolation or forced cancellation.
- The Qt-independent `SharedDesktopChatController.logout()` previously waited indefinitely for active conversation/history operations. It now signals active chat cancellation and waits only up to a validated configurable deadline (default 30 seconds, maximum one hour). If the deadline expires, it raises `TimeoutError`, leaves the conversation session/store open, and keeps the controller authorization gate closed; a later logout call can retry after the active operation unwinds.
- `SharedDesktopControllerBridge.logout()` now applies the same bounded-wait contract to active specialist operations and concurrent logout. On timeout, it leaves the specialist and chat dependencies open and keeps the bridge closed to new work, allowing a safe retry.
- Added tests for timeout with active chat reads, cancellation/drain retry, active specialist-operation timeout/retry, and invalid timeout values. Tests are authored only; no local test execution or CI success is claimed.
- This bounds only the bridge's waits for active operations. It does not forcibly terminate a blocked provider, backup, restore, or specialist call; it does not guarantee the underlying `specialist_controller.logout()` or resource `.close()` hooks themselves have bounded duration. The Qt single-thread pool can remain occupied by a hung operation. Process termination and provider-level connect/read/total deadlines remain separate requirements.


## Legacy task-store call-path and authority disposition

A focused source review of the legacy task implementations establishes the following module-level boundaries. This is not a claim that every caller or composition root has been exhaustively traced; repository code search did not return usable call-site results, so reachability must remain explicitly unverified rather than inferred from filenames.

| Store | Concrete behavior confirmed in source | Authority / lifecycle disposition |
|---|---|---|
| `DurableTaskEngine` (`core/durable_task_engine.py`) | Own SQLite goals/tasks/dependencies/events schema; public `claim()`, `heartbeat()`, `complete()`, `fail()`, `recover_expired_leases()`, cancellation and goal transitions. Claims are transactional and lease-bound. | No injected maintenance fence, owner-session predicate, approval record, or worker drain hook in this class. It does not execute task code itself. Treat as an isolated legacy queue; do not attach consequential handlers or use it for privileged actions. |
| `SQLiteTaskLifecycleStore` (`core/task_lifecycle.py`) | Stores versioned task state and a hash-chained event history; transitions are transactional; restart recovery changes `running` tasks to `recovery_required` rather than blindly replaying. | No claim/worker execution API or maintenance-fence injection. It is a state ledger, not proof of authorization or an execution engine. Keep non-consequential until a host-owned authorization and lifecycle contract is documented. |
| Agent `TaskStore` (`yuki_agent_runtime.py`) | Own SQLite queue with submit, caller-labelled approval, `claim_next()`, lease heartbeat, completion/failure/retry and cancellation. Approval stores an `approved_by` string, but this module does not resolve it to a live owner session or policy decision. | Claim path has no live-session validation, shared maintenance fence, or drain gate. The module explicitly delegates execution and tool permissions to a separate worker. Do not use this queue for consequential work without a separately authenticated worker service and atomic claim-time authority check. |
| Workspace task list (`yuki_tasks.py`) | JSON task-list CRUD protected by an inter-process file lock and atomic JSON replacement; tasks are simple descriptions/completion flags. | Personal checklist data only; not a durable agent queue, approval authority, or workflow source of truth. Keep it outside the control plane. |
| Coding task plans/evidence (`core/coding_tasks.py`, `core/coding_task_execution.py`) | Stores reviewed patch proposals and separate execution evidence. `execute_approved_coding_task()` verifies approved state, patch digest, reviewed workspace and executable allowlist, then delegates to a sandbox runner and calls an approval callback bound to task ID, patch digest and exact sandbox request. | This is the one reviewed legacy path that can launch a consequential operation (sandboxed code checks). The callback's session/policy binding is composition-owned; approval state is not atomically consumed, and evidence is written after execution in a separate DB. Keep disabled for unattended or privileged use until one-shot claim, live-session revalidation, shared fence, and uncertain-outcome reconciliation are implemented. |

### Containment rules

1. Do not migrate these stores into `WorkflowRunStore` by copying rows or treating their similarly named states as equivalent. Their idempotency, approval, lease, event and recovery semantics differ.
2. Do not add a global fence to constructors without first establishing one canonical private root and enumerating every caller. A per-store optional lock would create false confidence if any writer uses a different instance/path.
3. `DurableTaskEngine` and agent `TaskStore` may retain their transactional lease mechanics for isolated experiments, but their public claim APIs are not owner authorization. No production worker should consume them for consequential actions.
4. The lifecycle ledger can be retained as a non-executing record only if its writer and data-retention owner are explicit; its hash chain is tamper-evidence within that database, not an independent checkpoint or authorization proof.
5. The JSON checklist remains ordinary owner data and must not be presented as an agent execution queue.
6. Coding-task execution requires a separate remediation track because it crosses a sandbox execution boundary. A failed evidence write after a successful sandbox run is an uncertain outcome, not a safe retry signal.
7. Before any of these queues is re-enabled for production execution, add a verified composition/call-graph inventory covering imports, constructors, CLI/API/desktop entrypoints, schedulers, worker processes, recovery jobs, database paths and provider credentials. Current call-site reachability is not established by this source review.

No schema, runtime behavior, queue data, or deployment configuration was changed in this audit. No task was executed. Tests were not run locally.


## Coding-task one-shot execution blocker and hardening contract

A deeper read of `core/coding_task_execution.py`, `core/coding_tasks.py` and their tests confirmed that the coding-task execution function checks `state == approved`, patch integrity and workspace/command constraints, then invokes the sandbox and writes evidence afterward. It does not reserve or consume approval atomically, does not create a durable attempt before launch, and does not move the task into an executing/terminal state. The task can remain approved after a successful run, so sequential or concurrent callers can launch it more than once. Cancellation can race between the initial state check and sandbox launch. The approval callback is bound to task/patch/request but its live-session and policy semantics are composition-owned and unverified here.

A separate hardening contract was added at `docs/CODING_TASK_EXECUTION_HARDENING_CONTRACT.md`. It specifies an atomic one-shot attempt reservation, live-session/revocation-generation validation, immutable authorization envelope, runner receipt and fencing, explicit unknown-outcome recovery, idempotent evidence persistence, private-profile/maintenance-fence integration, and concrete concurrency/crash/revocation tests.

**No execution code was changed in this increment.** This is deliberate: adding a superficial `executing` flag or changing state after the sandbox returns would not close the check-to-launch race or the crash-after-effect/before-evidence gap. Keep coding-task execution disabled for unattended or privileged use until the attempt ledger and trusted runner boundary are implemented. No sandbox task was run and no database was migrated.


### Coding sandbox runtime verification follow-up

The Docker runner and argv builder were inspected after the one-shot execution audit. The argv builder has useful hardening (digest-pinned image syntax, disabled network, read-only root, read-only workspace by default, CPU/memory/PID limits, dropped capabilities and no-new-privileges). But `run_sandboxed_task()` does not call the existing `validate_runtime_capabilities()`; that validator itself deliberately refuses to promote self-reported Docker metadata or caller-supplied booleans into attestation because no trusted host verifier exists. A non-root Yuki process does not establish that the Docker daemon is rootless.

The whole workspace is bind-mounted. Its contents may include secrets or Unix sockets; a live workspace mount is not a reviewed immutable input snapshot. Container cleanup failure is surfaced, but cleanup confirmation is not a stable runner receipt or proof of absence of external effects after host-process loss.

This is now captured in `docs/CODING_TASK_EXECUTION_HARDENING_CONTRACT.md`. The deployment-owned runtime verifier, immutable workspace staging, runner receipt, and crash reconciliation are explicit enablement gates. No runner code or sandbox was changed or launched. Existing tests were not run locally.


### Immutable coding-task workspace staging contract

Added `docs/IMMUTABLE_CODING_WORKSPACE_STAGING.md` after confirming the current Docker runner bind-mounts the complete live workspace. The contract requires descriptor-relative no-follow enumeration and copying, strict regular-file allowlisting, secret/socket exclusion, bounded resource use, canonical manifest hashing, attempt-specific atomic publication, launch-time revalidation, a separate untrusted output-import path and cleanup tied to runner identity.

This is intentionally a design contract, not an implementation claim. A scan-then-mount of the original workspace or a generic recursive copy helper is not an acceptable substitute because of replacement races and filesystem metadata exposure. Windows reparse-point/ACL semantics need their own verified implementation; POSIX-only checks cannot establish Windows safety.

Coding-task sandbox execution remains disabled for unattended or privileged work. No sandbox was launched, no task data was copied, and no workspace migration was performed.


### POSIX immutable workspace staging prototype

Added `core/immutable_workspace_staging.py` and `tests/test_immutable_workspace_staging.py`. The first implementation is deliberately isolated from the coding-task runner. It accepts only an explicit relative-file allowlist; uses descriptor-relative no-follow traversal; rejects special files, symlinks, hardlinks, non-canonical paths and path collisions; bounds file counts/sizes/path depth; detects source mutation during copying; creates a private snapshot with a canonical SHA-256 manifest; reopens and verifies staged file contents; and seals the snapshot read-only before publishing it under an attempt-scoped random ID.

The prototype is POSIX-only and not production enablement. It is not wired into Docker execution, does not independently establish reviewed source-tree provenance or owner-session authorization, lacks a separate output-import pipeline and durable retention/reconciliation, and does not defend against a hostile process with the same OS UID replacing paths in the staging namespace. There is no Windows reparse-point/DACL implementation. Keep unattended and privileged coding-task execution disabled. Tests were authored but not run locally.

## Coding-task execution claim hardening

The coding-task runner now consumes approval through an atomic SQLite compare-and-swap before Docker launch. The claim is bound to the approved task version and reviewed patch digest; the patch bytes are re-hashed in the same database transaction. Only one concurrent caller can move a task from `approved` to `executing`. The generic transition API cannot enter or leave execution states.

After the sandbox returns, hash-only evidence is committed before the execution coordinator marks the task `completed`. If the runner errors, the process crashes, evidence persistence fails, or completion persistence fails, the durable state remains `executing` and a second invocation is denied. This is intentionally fail-closed: it prevents automatic replay but does not prove whether a command completed when the host lost its outcome.

**Recovery requirement:** an `executing` task is an uncertain outcome, not a lease that expires and not permission to retry. An owner must reconcile the sandbox/container and workspace effects using independent operational evidence before any manual disposition. No automatic reset to `approved` is allowed. The current evidence database and task database are separate SQLite transactions, so this design prevents duplicate starts but is not a cross-database atomic commit or tamper-proof execution receipt.

Regression coverage was added for single-winner concurrent claims, runner failure, evidence-write failure, and replay denial. Tests are authored but have not been run locally; CI must validate the exact branch head. This does not make the coding-task stores part of the canonical maintenance fence or enable consequential unattended execution.


## Coding-task stores: shared maintenance-fence adoption

The coding-task plan store and execution-evidence store now require an explicit
`MaintenanceFence` at construction. Every SQLite open is performed inside the
fence lease, and the lease remains held through transaction commit/rollback and
connection close. Constructors reject database paths outside the fence's
profile root and no longer create arbitrary parent directories.

Added `core/coding_task_stores.py` with
`build_coding_task_stores(profile_root)`. It validates the owner-private
profile, creates one fence, and injects the same instance into
`CodingTaskStore` and `CodingTaskExecutionStore`. The existing coding-task
execution tests now use one shared fence and cover shared authority identity
and rejection of paths outside the fenced root.

**Scope and limitations:** this is a composition primitive, not proof that any
production host currently constructs or exposes these stores. A repository
search found no caller of `execute_approved_coding_task()` or
`CodingTaskStore` in the default-branch code search; branch-local reachability
must still be confirmed before enabling the feature. The task and evidence
databases remain separate transactions: a shared maintenance fence coordinates
cooperating maintenance operations but does not provide atomic task/evidence
commit, durable runner receipts, or crash reconciliation. The existing
`executing` state remains non-replayable and requires owner reconciliation.
No automatic reset on restart is permitted.

Tests were authored but not run locally. CI must validate the exact branch
head. Coding-task execution remains disabled for unattended or privileged use;
no merge, migration, or deployment is authorized by this change.


## Desktop backup/restore handling for coding-task execution state

The desktop controller now acquires a `MaintenanceFence` rooted at the active
private desktop profile before its coding-task backup preflight and holds that
same persistent lock across the complete encrypted backup operation. Because
coding-task stores use the same profile-root lock path, their participating
SQLite operations cannot race the task/evidence snapshot.

The backup preflight validates the canonical pair
`coding-tasks.sqlite3` + `coding-task-evidence.sqlite3` when present. It fails
closed if only one database exists, either schema/integrity check fails, any
task remains in `executing`, or evidence refers to a missing/non-completed
task. Restore repeats the same validation against the isolated staging tree
before publication, so older or externally generated archives containing
unresolved execution state are not silently installed.

Regression tests were added for unresolved tasks, evidence mismatch, valid
completed evidence, restore rejection, and the authenticated desktop backup
path. Tests are authored but have not been run locally; CI is required.

**Boundary:** this only coordinates stores that use the canonical profile
fence and the two canonical database filenames. The configured workspace is
an additional backup root and is not protected by this profile fence; its
conversation database and other workspace writers still need a separately
defined maintenance/backup contract. This does not create a cross-database
transaction, prove production coding-task host wiring, or reconcile an
uncertain runner outcome. The backup is intentionally refused until an owner
has independently reconciled any `executing` task.

## Desktop workspace conversation writer joins the profile fence

A source trace of the actual Qt desktop composition found that the durable
conversation database lives at `workspace/conversations.sqlite3`, while the
desktop backup already holds the canonical profile-root maintenance fence.
The desktop conversation composition had not passed that fence into its
`ConversationStore`, leaving a first-party writer able to race the backup
snapshot despite the backup lock.

The desktop conversation composition now creates the same canonical
`MaintenanceFence(desktop_data_root())` used by desktop backup and passes it
through the desktop runtime into `build_conversation_application_runtime`.
The default `create_conversation_store` path injects it into
`ConversationStore`; each conversation-store operation therefore holds the
same persistent profile lock that backup holds across its full archive
operation. A regression test asserts the default desktop store receives the
profile fence.

**Boundary:** this closes the known first-party desktop conversation-store
writer gap only. It does not coordinate arbitrary external editors, other
processes that bypass the fence, or PostgreSQL conversation storage. SQLite
snapshots remain individually consistent, not a cross-database transaction.
The configured workspace may contain owner-managed files that are not
serialized by Yuki; do not claim a globally atomic snapshot of an externally
mutable workspace. Tests are authored but not run locally; exact-head CI is
required before treating this change as validated.

### Custom desktop conversation-store factory enforcement

The shared runtime previously passed the maintenance fence only when the
factory object was exactly the built-in `create_conversation_store`. A custom
factory supplied by desktop composition could silently ignore the fence while
the backup path appeared coordinated. The runtime now requires any custom
factory used with a maintenance fence to explicitly accept
`maintenance_fence` (or keyword arguments); otherwise composition fails
closed. The desktop composition test factory now forwards the fence, and a
regression test verifies that a legacy one-argument custom factory is rejected.
This is a local composition contract, not proof that a custom factory actually
uses the fence correctly; its implementation still requires review.

## Encrypted desktop memory joins the profile backup fence

The native desktop encrypted-memory composition now injects the canonical
`MaintenanceFence(desktop_data_root())` into `EncryptedMemoryStore`. Its
public read/modify/write operations (`add`, `list`, `delete`, `clear`)
and portable backup-key export acquire that fence. This prevents a memory
write from racing the backup's read of `memory.enc.json`, and serializes the
memory key envelope read with the same archive operation. The desktop backup
controller now acquires the profile fence before exporting the optional
portable memory key and holds it through coding-task preflight and archive
creation.

Regression coverage is authored for store lease acquisition, desktop memory
composition, and key export occurring while the backup fence is held.

**Boundary:** this coordinates the native desktop encrypted-memory writer.
It does not coordinate unrelated processes that directly replace
`memory.enc.json`, other memory backends, or keyring changes performed outside
Yuki. The OS keyring itself is not archived; only the opt-in key envelope is
included inside the encrypted backup. Tests are not run locally; exact-head CI
is required.

## Native desktop persistence reachability audit

The live Qt desktop startup path in `ui/desktop/launch.py::_build_services`
constructs `build_control_plane_stores(profile_root=root,
auth_database_name="auth.sqlite3", workflow_database_name="tasks.sqlite3")`.
That composition creates one fence and injects it into both the auth and
workflow stores. `TaskRuntime` consumes that `WorkflowRunStore`, and
`SpecialistAgentCoordinator` receives both the same workflow database path
and the exact same fence instance. Its plan and specialist-registry SQLite
transactions therefore participate in the desktop backup lease.
The authenticated conversation composition uses the same canonical profile
lock path for `workspace/conversations.sqlite3`; native encrypted memory now
uses it for `memory.enc.json` and optional portable key export.

The separately implemented `SQLiteTaskLifecycleStore` and
`DurableTaskEngine` were inspected but are not constructed by this live
desktop startup path. They remain standalone/other-runtime persistence
implementations; no speculative fence wiring was added to them in this pass.
Before either is adopted by a production composition, that composition must
inject a shared maintenance fence and prove its writers participate.

The desktop archive still includes the configured workspace tree, which can
contain arbitrary owner-managed files and external application state. Yuki's
fence coordinates only first-party stores explicitly wired to it; it cannot
promise a globally atomic snapshot against external workspace editors or
uncoordinated processes.

## Restore publication and durability failure semantics

Restore validates and fsyncs the isolated staging tree before publication.
When portable memory import is requested, the key is installed in the OS
keyring only after the staged ciphertext has been authenticated against it.
If directory publication then fails, restore attempts to remove only a key
entry newly created by that invocation. A pre-existing matching key is never
removed; a conflicting key is rejected.

There is a distinct post-publication failure window: if the destination rename
succeeds but syncing the destination's parent directory fails, the restored
profile and imported key may already be usable even though the operation
returns an error. The caller must inspect and verify the destination before
retrying; blindly retrying against the now non-empty destination is expected
to fail. Regression coverage now exercises this published-but-durability-
uncertain outcome. Filesystem durability remains platform/filesystem
dependent.


## Opt-in local worker coordinator joins its workflow maintenance fence

A follow-up trace found that `build_local_worker_runtime()` created a
`MaintenanceFence` and injected it into `WorkflowRunStore`, but omitted it
when constructing `SpecialistAgentCoordinator`. The coordinator writes
specialist registry and plan tables into the same `yuki_tasks.sqlite3`
database, so those writes could bypass a maintenance operation that correctly
held the worker profile lock.

The local worker composition now passes the exact same fence instance to the
coordinator. A regression assertion verifies object identity between
`built.coordinator.maintenance_fence` and
`built.runtime.store.maintenance_fence`.

This covers the canonical local-worker factory and the native desktop
composition only. Standalone coordinator constructors may still omit the
optional fence for isolated tests/tools; any production composition sharing a
database with fenced stores must inject the canonical fence. The fence
serializes cooperating database operations, not provider calls, external
processes, or separate SQLite transactions. Tests are authored but not run
locally; exact-head CI remains required.


## DurableTaskEngine expired-lease replay hardening (2026-11-28)

A focused review of `core/durable_task_engine.py` found that `recover_expired_leases()` previously returned an expired running task to `queued` whenever its attempt budget was not exhausted. A worker can perform an external side effect and then crash before persisting `complete()`; the lease timeout cannot determine whether the target committed that effect. Requeueing therefore created an automatic duplicate-side-effect path.

The recovery transition is now conservative and atomic: every expired running task becomes terminal `failed`, clears its lease, records the bounded marker `worker lease expired; outcome unknown`, and emits `task.lease_expired_outcome_unknown` with `automatic_replay: false`. The engine also fails blocked descendants and re-evaluates goal completion. The configured attempt budget is deliberately ignored for this crash-recovery case. Owner/operator reconciliation and any deliberate resubmission remain outside this queue API.

The generic `fail()` method still supports bounded retries when a worker explicitly reports a failure; callers must only use that path when they know retry is safe. The dedicated `DurableSkillWorker` accepts only `skill.invoke` tasks with `max_attempts=1`, but the engine itself remains a general queue primitive and does not authenticate an owner, enforce approval, sandbox handlers, or inject the canonical maintenance fence.

Reachability remains bounded: the in-repository `DurableSkillWorker` accepts the engine and guarded skill runtime by dependency injection, but this review did not establish a production host composition or scheduler that starts it. Treat this as an externally hostable execution surface, not a verified active production worker and not proof it is unused. Do not attach consequential handlers until a host supplies authenticated approval, a private-profile/fence composition, lifecycle drain, target-side idempotency or reconciliation, and deployment-specific backup/restore validation.

Regression coverage now asserts that an expired task with remaining attempts is marked outcome-unknown/failed, its stale worker cannot complete it, a second worker cannot reclaim it, and the event explicitly forbids automatic replay. Tests were authored but not run locally; CI status is checked separately. No task database was opened or migrated, no worker was started, and no task was executed.


## DurableSkillWorker host/authorization trace (2026-11-28)

Added `docs/DURABLE_SKILL_WORKER_HOST_AUTHORIZATION_AUDIT.md` after tracing the injected `DurableSkillWorker` → `ApprovedSkillRuntime` → `invoke_registered_skill()` path. The worker validates a fixed task type, exact payload shape, and single-attempt policy, but neither `DurableTaskEngine` nor the worker validates an authenticated owner/session. The runtime's `approve_invocation()` is a process-local grant with no actor, expiry, durable event, or session revocation; low-risk skills may execute without that grant. Registration approval is not invocation authorization. Skill code still executes in-process with ambient OS privileges.

The source-tree review found no verified production host: the CLI remains deny-all for tools, desktop startup does not construct this worker, and the stock web launcher does not start it. This remains `implemented / host-owner-unverified`, not production-active and not proof of unused. The worker also cannot forcibly cancel an in-flight synchronous skill if heartbeat renewal fails. No host wiring was added; consequential execution remains disabled pending authenticated task/dispatch authorization, durable one-shot approval, isolation, private-profile/fence adoption, drain, and target-side reconciliation.


## SQLiteTaskLifecycleStore reachability and control audit (2026-11-28)

Added `docs/SQLITE_TASK_LIFECYCLE_STORE_REACHABILITY_AUDIT.md`. The store's crash recovery is conservative: an explicit call marks interrupted `running` tasks `recovery_required`, never requeues them. Creation and state/event transitions are transactional; state changes are versioned; idempotency keys are payload-bound. However, no authenticated actor/session or approval proof is stored, callers holding the store can transition pending→approved→running, recovery is not invoked by a verified first-party host, and the SQLite operations bypass the canonical maintenance fence. The SHA-256 event chain is unkeyed and is only a tamper-detection consistency check, not authenticated audit evidence.

The installed CLI, reviewed desktop composition, and standard API runtime do not construct this store. The Phase 142/143 durable journal documentation refers to the separate authorized `TaskRuntime`/`WorkflowRunStore`, not this class. Repository code search returned no indexed references, which is bounded evidence and not proof against external host use. Classification: standalone / external-host reachability unverified. No speculative host wiring or runtime change was made; require private-profile/fence ownership, authenticated one-shot approval, recovery exclusivity, isolation, and backup/reconciliation before consequential use.


## File-backed `yuki_tasks.py` checklist audit (2026-11-28)

Added `docs/FILE_BACKED_TASK_CHECKLIST_AUDIT.md`. Unlike the independent agent queues, `yuki_tasks.py` is only a manual checklist used by the legacy `yuki.py` terminal prototype; it has no handler/worker/approval/dispatch path. Its CRUD operations already use the shared cross-process JSON file lock and stale-snapshot comparison. The adjacent JSON and lock files are owner-only on POSIX, but the existing source-checkout parent directory is not made private by `ensure_private_directory()`; keep sensitive content out and plan an explicit private-profile migration rather than silently changing the legacy path.

Two data-integrity defects were fixed: malformed list entries were silently filtered (so a later write could destroy them), and `save_tasks()` could overwrite a newer list without compare-and-swap. The loader now fails closed on malformed/duplicate/non-positive IDs, and `save_tasks()` requires an expected snapshot. Added `tests/test_yuki_tasks.py` for CRUD, stale writes, CAS save, corruption preservation, and invalid IDs. Tests authored, not run locally. No checklist migration or task execution occurred.


## Legacy terminal provider and persistence composition audit (2026-11-28)

Added `docs/LEGACY_TERMINAL_PERSISTENCE_PROVIDER_AUDIT.md`. The root-level
`yuki.py` compatibility terminal is distinct from the installed
`yuki` command (`yuki_cli:main`). Its JSON notes/checklist/learning/audit
files remain adjacent to source modules; the durable task and skill approval
SQLite files remain under a source-relative `.yuki` directory. The shared
JSON lock helper secures individual files but assumes a private parent and
does not repair existing directory permissions. The legacy SQLite callers
do not establish the canonical private profile or maintenance fence.

Legacy note persistence was hardened without changing its path: it now uses
the shared lock and durable atomic JSON writer, rejects corrupt state rather
than overwriting it as empty, and performs snapshot compare-and-swap for
command-level add/clear. Regression tests cover round-trip, stale writes, and
corruption preservation. Learning history and audit logging already use the
shared JSON lock/atomic writer, but their loaders still skip malformed
learning entries or treat unreadable audit data as empty; those remain
integrity follow-ups. Their parent directories are not proven private.

The legacy provider manager uses environment-backed credentials and keeps
selection in memory; no selection persistence was found in the inspected
terminal path. Its manager lifecycle has a close API, but the legacy main loop
does not currently close it explicitly. The legacy terminal also exposes
`/skill invoke`, `/tool`, and `/durable run`, unlike the installed
conversation-only CLI. The durable worker/approval stores remain unfenced and
must not be used for consequential or unattended actions.

No data migration, retirement, provider request, task execution, or deployment
occurred. Tests were authored but not run locally; CI must validate the exact
branch head. Preserve legacy filenames and behavior until an explicit,
backed-up private-profile migration and compatibility decision is approved.


## Legacy learning and audit log corruption handling (2026-11-28)

- `yuki_learning.py` now rejects malformed records and non-string fields on
  both load and save instead of silently dropping malformed history entries.
- `yuki_security.py` now distinguishes a missing first-use audit file from
  unreadable/corrupt data, validates event shape/types, and refuses to append
  when existing audit history is malformed. Existing bytes are preserved.
- Added regression coverage in `tests/test_yuki.py` for malformed learning
  records, invalid field types, corrupt audit JSON, malformed audit events,
  and preservation of original bytes after refused writes.
- Updated `docs/LEGACY_TERMINAL_PERSISTENCE_PROVIDER_AUDIT.md`.
- File locations and valid record formats remain unchanged. This does not
  establish private parent-directory permissions, authenticated/tamper-proof
  audit integrity, or migration into the canonical private profile.
- Tests are authored but not run locally. No merge, migration, or deployment.

## Legacy terminal provider lifecycle follow-up (2026-11-28)

The root-level compatibility terminal now closes its `ProviderManager` in a `finally` block, including normal quit/EOF/interrupt and startup failures after manager construction. A bounded warning is printed if adapter shutdown fails; provider exception details are not exposed and cleanup failure does not mask an existing startup/runtime exception. Regression tests cover normal exit, startup failure, and close failure. This does not provide a retry supervisor after process teardown, drain future asynchronous/background provider work, or resolve the legacy durable-task/skill-approval profile and authorization gaps. Tests authored, not run locally; no deployment or merge.

## Legacy terminal SQLite confinement audit (2026-11-28)

- Traced the root-level terminal's actual /durable and /skill command paths to DurableTaskEngine and SkillApprovalStore.
- Both databases remain source-relative under .yuki/. Their constructors create/open SQLite directly; neither validates the canonical private profile or participates in the maintenance fence.
- The legacy command dispatcher exposes durable worker execution and skill invocation without the canonical authenticated owner/session composition. Existing one-attempt/replay controls do not establish that authorization boundary.
- Added docs/LEGACY_TERMINAL_SQLITE_CONFINEMENT_AUDIT.md with exact paths, reachable commands, confinement gaps, and a safe migration decision.
- No code, paths, schemas, permissions, or user data changed. No SQLite database was opened or created.
- Keep legacy execution experimental and unavailable for consequential/unattended use until all writers, profile confinement, owner/session authorization, fence participation, and worker lifecycle are handled together. Tests not run locally; no merge or deployment.


## Legacy terminal SQLite caller and sidecar trace (2026-11-28)

Added `docs/LEGACY_TERMINAL_SQLITE_CALLER_SIDECAR_TRACE.md`. The root
`yuki.py` dispatcher reaches the durable task engine from goal create,
submit/list/cancel, and `/durable run`; the latter performs recovery and
executes one registered skill in-process. The skill approval store is
constructed by the shared legacy skill-runtime builder for every `/skill`
command, including list/inspection. Persistent manifest approval and the
process-local one-shot invocation grant are distinct, and neither establishes
authenticated owner/session authorization.

The trace records the exact source-relative SQLite paths, constructor and
connection lifetimes, potential SQLite journal/WAL/SHM sidecars, and the
limits of repository code search. No runtime guard was added: blocking only
the worker would leave other writes and invocation paths outside the profile
and maintenance boundary; a path guard alone would not establish authorization
or safe lifecycle. Preserve existing data and behavior pending an explicit,
backed-up migration. No database or filesystem was inspected or modified;
tests not run locally; external callers and application-wide quiescence remain
unverified.


## Legacy terminal JSON persistence boundary (2026-11-28)

Added `docs/LEGACY_TERMINAL_JSON_PERSISTENCE_BOUNDARY_AUDIT.md`. The root
compatibility terminal stores memory, task list, learning history, and audit
events in four JSON files beside the source. Their shared helper has per-file
locks and atomic replacement, but existing parent directories are deliberately
left unchanged and the per-file locks are not the canonical profile fence.
Reads also follow the target path; the audit did not establish a symlink policy
for these legacy JSON targets.

Do not treat these files as covered by the profile backup fence or as
owner-private merely because the helper creates missing directories securely.
No paths or permissions changed, and no data files were opened or modified.
External writers and other installed copies remain untraced; tests not run
locally.


## Legacy skill-pack installation boundary (2026-11-28)

Added `docs/LEGACY_SKILL_PACK_INSTALLATION_BOUNDARY_AUDIT.md`. The root
compatibility terminal exposes a skill-pack installer whose default directory
is under `data_root()`; for source checkouts that may be the repository root,
not the canonical private profile. The loader validates data-only packs and
marks them high-risk, but install/remove/load do not participate in the
canonical maintenance fence. Installation's existence check plus
`os.replace` is not a proven atomic no-overwrite operation under concurrent
installers. No runtime or pack files changed; no pack was installed, removed,
or invoked. Tests not run locally.


## Legacy plugin discovery execution boundary (2026-11-28)

Added `docs/LEGACY_PLUGIN_DISCOVERY_EXECUTION_BOUNDARY_AUDIT.md`. Discovery is
opt-in and exact-module allowlisted, but importing trusted Python modules runs
their code with Yuki's process privileges. Invocation approval does not contain
import-time effects or sandbox module code. No plugin was imported or executed;
no runtime changes were made; tests not run locally.


## Configuration path and permission boundary (2026-11-28)

Added `docs/CONFIGURATION_PATH_AND_PERMISSION_BOUNDARY_AUDIT.md`.
The explicit config-path loader and existing-config read path do not validate
symlink status, regular-file type, owner, or restrictive permissions; explicit
`load_config(path=...)` also bypasses first-run private-directory creation.
No config was read or changed, no runtime changes were made, and tests were not
run locally.


## Configured workspace root and secure-access boundary (2026-11-28)

Added `docs/CONFIGURED_WORKSPACE_ROOT_SECURITY_BOUNDARY_AUDIT.md`.
Workspace path resolution accepts absolute destinations and follows symlinks;
it is not itself a private-root or filesystem authorization mechanism. The
repository does contain stronger resolver, descriptor-relative file access,
and immutable staging primitives, but their existence does not prove all
workspace consumers use them. No workspace data was read or changed; tests
were not run locally.


## Workspace caller filesystem integration (2026-11-28)

Added `docs/WORKSPACE_CALLER_FILESYSTEM_INTEGRATION_AUDIT.md`.
Specialist read tools and approved edits perform useful path containment,
authorization, audit, and digest checks, but their final filesystem operations
remain path-based and are not descriptor-anchored. Legacy file/canvas helpers
also do not inherit the secure workspace API. Findings are integration gaps;
no exploit or deployment impact is asserted. No runtime code changed and no
local tests were run.


## Legacy tool registry import and dispatch (2026-11-28)

Added `docs/LEGACY_TOOL_REGISTRY_IMPORT_AND_DISPATCH_AUDIT.md`.
The legacy terminal imports a registry that creates a source-relative workspace
directory at module import. Its current `/tool` path fails closed because it
does not supply the owner/session identity required by GuardedToolRuntime;
direct `run_tool()` remains an older unguarded dispatch function for any
external caller. No workspace data or directory was touched; no runtime code
changed and no local tests were run.


## Secure workspace API guarantee and adoption (2026-11-28)

Added `docs/SECURE_WORKSPACE_API_GUARANTEE_AND_ADOPTION_AUDIT.md`.
The POSIX descriptor-backed API provides no-follow component traversal,
bounded regular-file reads, and descriptor-relative atomic publication beneath
an opened workspace root. It is not itself an actor-authorization or
owner-private-root validator. Its current write contract also lacks an
expected-digest / expected-absent condition: a concurrent writer can replace
the final entry after the observed-type check and have its update overwritten
by publication. This is a lost-update / stale-precondition gap, not evidence
of a workspace escape.

The previously traced specialist, approved-edit, legacy file, and canvas
callers remain path-based and do not inherit these guarantees. Recommended
next steps are conditional publication, composition-level root privacy and
authorization, descriptor-backed migration, and adversarial race tests.
No runtime changes or workspace data access occurred; tests were not run
locally.


## Secure workspace handle lifecycle race (2026-11-28)

Added `docs/SECURE_WORKSPACE_HANDLE_LIFECYCLE_RACE_AUDIT.md`.
`SecureWorkspaceFiles` does not synchronize `close()` with active operations:
an operation validates the shared root descriptor and later duplicates it,
while another thread can close it in between. Descriptor-number reuse could
cause duplication of an unrelated open descriptor before relative traversal.
This is a potential cross-directory confinement failure if a handle is shared
and concurrently closed; actual host sharing/reachability is unverified.

Required fix: atomically acquire a per-operation root-FD duplicate under a
lifecycle lock, have all traversal use that duplicate, and have close mark the
handle closed under the same lock. Add deterministic close/read/write
concurrency tests. This is distinct from the conditional-publication
lost-update issue. No runtime code changed; tests were not run locally.


## Immutable workspace snapshot no-clobber publication (2026-11-28)

Added `docs/IMMUTABLE_WORKSPACE_SNAPSHOT_PUBLICATION_RACE_AUDIT.md`.
The staging prototype checks a random snapshot destination for absence and
then publishes with ordinary descriptor-relative `os.rename`. A concurrent
writer with access to the staging namespace can create that name between the
check and rename; ordinary rename is not an atomic no-replace guarantee.
This contradicts the unconditional "existing attempt destinations are never
overwritten" statement. Random naming reduces accidental collision but is not
a concurrency primitive.

Require a verified atomic no-replace publication primitive, fail-closed
platform/filesystem behavior, and a deterministic collision-race test. This
is distinct from the SecureWorkspaceFiles shared-descriptor close race.
No runtime code changed; tests were not run locally and no staging data was
created or modified.


## Immutable workspace snapshot post-rename durability uncertainty (2026-11-28)

Extended `docs/IMMUTABLE_WORKSPACE_SNAPSHOT_PUBLICATION_RACE_AUDIT.md`.
After `os.rename` succeeds, the implementation marks the snapshot published
and then fsyncs the destination directory. If that fsync fails, it raises a
generic creation error but intentionally skips cleanup. The caller therefore
does not receive the generated snapshot ID even though the destination may
exist. A blind retry can create duplicate/orphaned snapshots.

Require an explicit post-publication-uncertain outcome carrying the generated
ID, exact-destination manifest reconciliation, and fault-injection tests for
directory-fsync failure. This is separate from the no-replace collision race.
No runtime code changed; tests were not run locally and no staging data was
created or modified.


## Immutable workspace snapshot cleanup observability (2026-11-28)

Added a cleanup finding to
`docs/IMMUTABLE_WORKSPACE_SNAPSHOT_PUBLICATION_RACE_AUDIT.md`.
When staging fails before publication, `_remove_tree_at` cleanup errors are
caught and discarded. A partially removed temporary tree can remain without
the caller learning cleanup was incomplete. This is primarily a private
profile disk-retention/recovery observability gap, not a demonstrated
cross-user disclosure.

Preserve the primary failure while surfacing a structured cleanup-incomplete
signal, add safe orphan reconciliation, and fault-inject partial unlink/rmdir
failures. No runtime code changed; tests were not run locally and no staging
data was created or modified.


## Immutable workspace snapshot whole-tree consistency (2026-11-28)

Added a finding to
`docs/IMMUTABLE_WORKSPACE_SNAPSHOT_PUBLICATION_RACE_AUDIT.md`.
The staging prototype checks each file's metadata before and after copying,
but it does not freeze or revalidate one source-tree identity across the
entire multi-file operation. Concurrent edits between file copies can yield
a manifest combining different workspace generations even though each
individual copy passed its stability check.

Bind staging to an immutable reviewed Git tree/commit and exact approved
patch, or a canonical reviewed manifest; reject unreviewed dirty/untracked
inputs; and add deterministic multi-file mutation tests. Per-file stability
checks remain useful but do not prove a coherent tree snapshot. No runtime
code changed; tests were not run locally and no staging data was accessed.


## Workspace snapshot execution adoption (2026-11-28)

Added `docs/WORKSPACE_SNAPSHOT_EXECUTION_ADOPTION_AUDIT.md`.
The ordinary Docker coding-task runner still passes its caller-supplied live
workspace to the Docker argv builder. The workspace is mounted read-only by
default, but becomes read-write with the `write_workspace` capability.
Approval binds the resolved path, not an immutable source-tree or staged
manifest digest. The runner does not invoke the immutable staging prototype.

This is an execution-boundary integration blocker, not evidence of a
production compromise or an executed task. Production/unattended coding
execution must remain disabled until the runner accepts only a verified
attempt-specific snapshot, binds approval to its manifest digest, and uses a
separate constrained output-import path. No runtime code changed and no
sandbox was launched.


## Workspace capability-to-mount enforcement (2026-11-28)

Added `docs/WORKSPACE_CAPABILITY_ENFORCEMENT_AUDIT.md`.
`SandboxPolicy` defines explicit `read_workspace` and `write_workspace`
capabilities, but Docker argv construction always mounts the supplied host
workspace and only varies read/write mode. The default policy has no
capabilities, yet still exposes workspace contents read-only. This is a
least-privilege policy enforcement gap.

Make the mount conditional on explicit access capability; use a private
empty scratch directory when neither is granted. Bind the effective mount
plan into approval. No runtime code changed and no sandbox was launched.


## Sandbox approval payload completeness (2026-11-28)

Extended `docs/WORKSPACE_CAPABILITY_ENFORCEMENT_AUDIT.md` with an approval
integrity finding. `SandboxExecutionRequest` omits effective capabilities,
network mode, maximum process count and the mount plan, although those values
change the Docker execution. In particular, `write_workspace` produces a
read-write bind mount without that privilege being visible in the approval
payload. Approval must bind one immutable execution plan used by both the
approver and argv builder, including all mounts, limits, image/snapshot
identity and output policy. No runtime code changed and no sandbox was
launched.


## Sandbox image ENTRYPOINT approval fidelity (2026-11-28)

Extended `docs/WORKSPACE_CAPABILITY_ENFORCEMENT_AUDIT.md`: Docker's image
ENTRYPOINT remains active when the runner appends the approved command after
the image reference. Thus the executable actually started may differ from
the command represented in `SandboxExecutionRequest`. Require an explicit
verified entrypoint contract and bind the effective process argv to the same
immutable execution plan used for approval and launch. No runtime code changed.


## Sandbox runtime attestation enforcement (2026-11-28)

Added `docs/SANDBOX_RUNTIME_ATTESTATION_ENFORCEMENT_AUDIT.md`. The sandbox module has a deliberately fail-closed runtime capability validator, but neither `run_sandboxed_task()` nor `build_docker_argv()` invokes it. The runner accepts no trusted attestation input and can proceed to approval and Docker launch without establishing the host isolation contract. Docker-info parsing is explicitly diagnostic and untrusted, not attestation.

Keep sandbox execution disabled until a host-originated verifier is mandatory in the composition path. Bind verifier provenance, freshness, daemon/runtime identity and policy coverage into the immutable approved execution plan; revalidate the selected daemon immediately before launch. Tests must prove missing, invalid, stale or mismatched attestation blocks both approval and Docker process creation. No runtime code changed, no daemon was contacted, no sandbox was launched, and tests were not run locally.


## Docker daemon identity stability (2026-11-28)

Added `docs/DOCKER_DAEMON_IDENTITY_STABILITY_AUDIT.md`. The coding-task runner reads Docker endpoint/context/TLS environment variables only after approval when launching, then independently rereads them during cleanup. The approval payload does not bind a daemon identity. If process environment or Docker context selection changes between these calls, cleanup may target a different daemon from launch; a successful removal there does not establish cleanup of the launched container. This is a source-level consistency gap, not evidence of an actual environment mutation or orphaned container.

Resolve and verify daemon identity at the trusted boundary, bind it to the approved execution plan, and use the same captured immutable connection configuration for launch, inspection and cleanup. Confirm and remove the exact container ID on that same daemon; reconcile uncertain outcomes. Tests should mutate environment/context after approval and between launch and cleanup. No runtime code changed, no daemon/container was contacted or launched, and tests were not run locally.


## Sandbox container cleanup ownership (2026-11-28)

Added `docs/SANDBOX_CONTAINER_CLEANUP_OWNERSHIP_AUDIT.md`. The runner generates a random container name and later force-removes by that name without verifying container ID, attempt-specific ownership labels or daemon identity. If launch fails because the name is already occupied, cleanup can remove the pre-existing container; a same-daemon actor could also race name selection. Randomness reduces accidental collision but is not an ownership proof. This is a source-level potential unintended deletion, not evidence of an observed collision or malicious daemon actor.

Use daemon-created container IDs, attempt-specific ownership labels/tokens, and the same verified daemon identity for inspection and deletion. Never remove by name when ownership is uncertain; record and reconcile uncertain cleanup. Add collision, concurrent claim, mismatched label and daemon identity tests. No runtime code changed, no daemon contacted, no container created/removed, and tests were not run locally.


## Docker CLI executable trust (2026-11-28)

Added `docs/DOCKER_CLI_EXECUTABLE_TRUST_AUDIT.md`. Both Docker launch and cleanup use the bare executable name `docker`; the runner forwards inherited `PATH`. Executable selection is therefore environment-dependent, and a substituted executable would run on the host outside container isolation. Actual exploitability depends on whether deployment environment ownership permits PATH manipulation; no such access was established by source review.

Resolve an absolute Docker CLI path from trusted administrator-controlled configuration, validate its identity and replacement protections, use it for all operations, and bind executable identity into the approved execution plan. Add tests proving malicious PATH cannot alter executable selection and untrusted/missing configuration fails before approval. No runtime code changed, no host executable or Docker daemon invoked, no container created/removed, and tests were not run locally.


## Sandbox output-reader lifecycle (2026-11-28)

Added `docs/SANDBOX_OUTPUT_READER_LIFECYCLE_AUDIT.md`. The runner joins its output reader for two seconds, closes stdout if still alive, then joins one more second without checking whether the thread terminated. It may therefore proceed to cleanup and return a normal result while the daemon reader remains alive. A retained pipe writer can keep a read blocked; actual occurrence depends on runtime/platform pipe ownership and was not established. Require deterministic reader shutdown before result construction; return a typed uncertain-output failure if termination cannot be confirmed; test an inherited writer keeping the pipe open. No runtime code changed, no daemon contacted, no container created/removed, and tests were not run locally.


## Sandbox workspace mount path TOCTOU (2026-11-28)

Added `docs/SANDBOX_WORKSPACE_MOUNT_PATH_TOCTOU_AUDIT.md`. The builder validates/resolves a workspace pathname and embeds that pathname in Docker's bind-mount argument; the runner then invokes approval and launches Docker, while the daemon resolves the source path later. No stable directory identity is bound across those stages. A local actor able to mutate the relevant parent/path in that interval could substitute the mounted directory. Exploitability depends on deployment filesystem permissions; source confirms the check/use gap, not attacker access. Remediate by using a securely staged immutable snapshot, protecting its parent against replacement, binding its identity into approval, and race-testing path replacement. Distinct from the existing live-workspace/snapshot-adoption finding. No runtime code changed; no daemon or container activity; no local tests run.


## Sandbox primary-failure preservation (2026-11-28)

Added `docs/SANDBOX_PRIMARY_FAILURE_PRESERVATION_AUDIT.md`. If execution raises and the `finally` cleanup then fails, raising from the finally block replaces the primary exception. For example, a timeout/shutdown failure can be hidden by a subsequent container-removal failure. Preserve both outcomes in a typed error or exception group/chaining while continuing to fail closed; add simultaneous-failure tests. No runtime code changed; no Docker daemon/container activity; no local tests run.


## Docker diagnostics JSON nesting depth (2026-11-28)

Added `docs/DOCKER_DIAGNOSTICS_JSON_DEPTH_AUDIT.md`. `parse_docker_info()` caps payload bytes and catches ValueError/UnicodeError from `json.loads`, but not RecursionError. Deeply nested JSON can therefore escape the documented SandboxRuntimeError contract despite being under the byte limit. Catch and normalize recursion failures, ideally enforce a defined maximum nesting depth, and add a small deeply nested payload test. Scope is diagnostic parser robustness; no evidence this parser is an execution authorization control or receives attacker-controlled network input. No runtime code changed; no daemon/container activity; no local tests run.


## Docker security-option diagnostic token parsing (2026-11-28)

Added `docs/DOCKER_SECURITY_OPTION_DIAGNOSTIC_PARSING_AUDIT.md`. `parse_docker_info()` uses substring matching for reported SecurityOptions (for example, `"name=rootless" in item`), so lookalike text such as `not-name=rootless` or `xname=seccomp` can create false-positive diagnostic flags. Parse exact comma-separated `name=<value>` tokens and add negative/positive tests. This is diagnostic accuracy, not evidence of an execution authorization bypass; daemon metadata remains explicitly untrusted. No runtime code changed; no daemon/container activity; no local tests run.
