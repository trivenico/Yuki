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
