# Worker, Workflow, and Notification Outbox Lifecycle Audit

**Review date:** 2026-11-26  
**Branch:** `research/reference-projects-integration-assessment`  
**Reviewed commit:** `9e3206429a8c2b47a7e9d6763344d5719ed8a019`  
**Scope:** Read-only inspection of the current branch's workflow/task store, task runtime, specialist worker, finite-cycle supervisor, proactive-alert outbox, API runtime, desktop conversation runtime, and repository tree. This is a targeted source audit, not a complete repository inventory or runtime test.

## Correction to the preceding inventory

The preceding inventory incorrectly described a separate PostgreSQL workflow/goal/outbox repository/runtime as present on this branch. The recursive Git tree at the reviewed commit contains `core/postgres_conversation_store.py`, but no separate PostgreSQL workflow repository, PostgreSQL goals repository, or PostgreSQL workflow/outbox runtime. The distributed workflow/control-plane implementation must not be assumed to exist based on earlier PR-series descriptions. The workflow/task journal inspected here is SQLite-backed (`automation/workflow_store.py`). The PostgreSQL conversation store is a distinct persistence path.

## Confirmed workflow/task persistence

- `WorkflowRunStore` owns one SQLite file, calls `prepare_private_sqlite_path()`, enables WAL and `synchronous=FULL`, and opens/closes a connection for each operation. It does not expose a store-wide `close()`, writer-drain or maintenance interface.
- The same database stores workflow definitions/run state/leases/approval metadata and task event history. These tables are one physical consistency unit.
- Mutating operations use explicit transactions, including `BEGIN IMMEDIATE` for approval/event transitions and event appends. State changes use version checks/CAS. This protects individual database operations, not application-wide maintenance.
- Expired leases are marked `interrupted` and an event is appended atomically. The code explicitly avoids automatic replay. Retry creates a new run with a fresh idempotency key and fresh approval state.
- `TaskRuntime` is a facade over this store, not an independent database or worker process. It enforces an injected authorization callback and delegates lease/state/event mutations to the store.

## Worker execution and shutdown boundary

- `WorkerSupervisor.run_cycle()` is finite and synchronous: recover a bounded set of expired leases, then process a bounded task batch. Its module documentation says it does not create background threads or automatically retry failed/interrupted tasks. A host scheduler/service must own invocation and shutdown.
- `SpecialistTaskWorker.run_concurrently()` creates a bounded in-process `ThreadPoolExecutor` for one batch. The executor context waits for submitted workers to finish before returning; there is no persistent worker pool or supervisor stop/drain API in this class.
- The current specialist handlers are deliberately narrow: bounded text input/output through a provider adapter; they do not execute generated code, run shell commands, perform implicit network access, or modify the workspace. Provider calls can nevertheless be in flight while an operator cancels a task.
- `run_once()` claims a task and calls the handler synchronously. It does not renew the task lease while the provider call is running. Completion checks the current lease and version; if the lease expires, completion is rejected. The worker notices cancellation after the handler returns, but cancellation does not interrupt the in-flight provider request.
- Therefore a long-running provider call can outlive its lease, be marked interrupted by a concurrent recovery cycle, and still consume provider resources until it returns. Current handlers are text-only, which limits side effects; this is not a safe pattern to generalize to future side-effecting agents.
- Before enabling long-running or side-effecting workers, add bounded provider timeouts/cancellation, lease renewal or a maximum execution time strictly below the lease, and target-side idempotency/fencing for any external action. A database lease alone cannot stop an already-running external request.

## Proactive notification outbox

- `ProactiveAlertStore` is a separate SQLite database with alert history and `proactive_notification_outbox`. It uses per-operation connections and `BEGIN IMMEDIATE` for observation/claim/settlement; it does not expose a store-wide close/drain/maintenance contract.
- `observe()` records a due notification and outbox row in one transaction. It advances `last_notified` before delivery, so the documented behavior is at-most-once notification attempts at observation time, not guaranteed delivery.
- `claim_notification()` leases pending or expired-claimed rows, increments attempts and returns a one-time lease token while persisting only its hash. `settle_notification()` must be inspected together with the actual host delivery caller before claiming end-to-end retry or delivery guarantees.
- The store module explicitly says it does not poll, schedule, send notifications, or retry tasks. No dispatcher/host delivery caller was established in this targeted pass. Treat outbox delivery ownership, retry scheduling, shutdown, and duplicate suppression across an external provider as unresolved.
- Retention deletes older delivered/failed outbox rows and old alert history. Backup/restore must consider pending/claimed rows and avoid blindly replaying notifications after restore.

## Runtime composition and shutdown

- `ConversationApplicationRuntime.close()` closes only its conversation store.
- `ConversationRuntime.close()` closes the shared conversation application. API runtime accepts separately injected authentication, pairing/inference, income, OAuth, MCP and evaluation services; the conversation runtime does not own or close those injected services.
- Desktop conversation runtime similarly owns the conversation store only; provider lifecycle belongs to the outer desktop process. This is an explicit narrow ownership contract, not a global application shutdown coordinator.
- The inspected security monitor is in-memory, explicitly enabled, caller-fed, read-only and non-persistent. It starts no capture or background work and clears its in-memory alerts when disabled. Do not include it as a durable store, but do include its host-provided sensors in process shutdown inventory if background sensors are later introduced.

## Maintenance and backup implications

1. Add the workflow journal and its event table as one SQLite consistency group in the canonical store manifest.
2. Add the proactive-alert database as a separate consistency group; record pending outbox state and its external delivery-provider/idempotency dependencies.
3. Record worker supervisors and scheduler invocations as writers even though they do not own a persistent connection. Maintenance must stop new cycles, wait for active provider calls, and prevent stale workers from resuming.
4. Treat API/desktop injected services as independently owned until composition is changed to a top-level resource owner with explicit close/drain methods.
5. Do not implement a global lock around only the conversation store. Every SQLite writer, JSON writer, worker claim/settlement path and external side-effect dispatcher must participate.
6. Do not describe the workflow store as PostgreSQL-backed or distributed. Current worker claims are coordinated through the SQLite store and its transaction semantics; deployment across multiple hosts requires a separate, reviewed backend and fencing design.

## Readiness decision

This pass corrected the repository inventory and identified the finite-cycle worker and outbox boundaries, but it did not locate every host scheduler or notification delivery caller. No code paths were changed, no database was opened or modified, no notifications were sent, and no tests were run locally. The global store inventory remains incomplete. Keep backup/migration operator-controlled and do not claim coordinated maintenance, automatic recovery, or end-to-end notification delivery guarantees.


## Legacy provider-backed task worker lease gap closed

A source-tree call-site scan found a separate legacy execution path in
`yuki_provider_worker.create_provider_handlers()`. Unlike the specialist
worker and web agent adapter, it resolved the selected raw provider once and
then invoked `provider.generate()` without a request-scoped manager lease.
That allowed manager shutdown to race an in-flight legacy task request.

The handler factory now requires `lease_adapter()` from its provider manager,
leases the adapter around the initial availability check, and acquires a fresh
lease around every complete synchronous generation call. A manager without
the lease contract is rejected at composition time. Tests cover shutdown
attempts during generation and fail-closed behavior for legacy managers
without lease support.

This is an additional legacy task queue integration, not proof that the queue
is part of the default desktop, web, or CLI startup path. It remains a separate
persistence/authorization domain; provider leasing only protects adapter
lifetime and does not grant task approval, unify task stores, or guarantee
cancellation of an already-running provider request. Tests are authored but
not run locally; exact-head CI status must be checked separately.
