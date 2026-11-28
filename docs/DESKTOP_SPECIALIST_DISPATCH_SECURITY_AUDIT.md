# Desktop specialist dispatch security audit

**Status:** source-traced and partially remediated on the integration branch. This is not approval to enable background specialist execution.

## Verified desktop path

1. `ui/desktop/launch.py::_build_services()` constructs authentication and `WorkflowRunStore` through `build_control_plane_stores()`, which owns the private profile's `MaintenanceFence`.
2. The desktop creates `TaskRuntime` with an authenticated permission callback and `SpecialistAgentCoordinator` over the same `tasks.sqlite3` file.
3. `CompanionApplicationService.propose_agent_task()` asks the selected provider for a bounded JSON task decomposition. The parser rejects extra task fields and unconditionally sets `requires_approval=True`; provider output cannot opt a task out of approval.
4. `SpecialistAgentCoordinator.submit()` persists a proposal. `advance()`, reached through the explicit desktop dispatch action, creates durable workflow tasks; it does not execute specialist handlers.
5. The desktop task-review dialog calls `approve_task()` for the exact displayed task version. `WorkflowRunStore.approve_with_event()` records the current session hash and a bounded expiry in the same transaction as the approval event.
6. `claim_next()` filters out tasks that require approval until approval metadata is present and unexpired, and also excludes tasks whose approving session has been revoked.
7. The native desktop startup does not construct `SpecialistTaskWorker`, `build_provider_specialist_handlers()`, or a worker supervisor. The approval UI explicitly reports that approval does not start a worker. Thus the traced desktop path queues and approves tasks but does not currently execute them.

## Finding fixed: coordinator bypassed the desktop backup fence

The coordinator stores `agent_plans`, `agent_plan_subtasks`, and `specialist_agents` in the same `tasks.sqlite3` database used by `WorkflowRunStore`. Before this change, its private `_connect()` opened SQLite directly and did not acquire the profile maintenance lease. A plan/registry transaction could therefore overlap a desktop backup even though workflow-store operations were fenced.

Changes:
- `SpecialistAgentCoordinator` accepts the host's explicit `MaintenanceFence`.
- When supplied, it validates that its database is directly beneath that fence's profile root.
- It acquires the lease before opening SQLite and retains it through transaction commit/rollback and connection close.
- Desktop `_build_services()` injects the exact fence returned by `build_control_plane_stores()`.
- Regression tests cover lease participation and rejection of a database outside the fenced profile.

The optional constructor argument preserves compatibility for standalone/test compositions. Such unfenced compositions are not covered by the desktop backup guarantee; production hosts must inject the canonical fence. The fence coordinates only cooperating processes and does not make separate SQLite databases atomic.

## Remaining security boundaries

- The separate guarded approval gateway/composition is still not wired into this specialist workflow. Specialist tasks use the existing workflow-store approval mechanism; do not describe the two approval systems as unified.
- The desktop currently has no specialist worker. Do not add one as a background convenience until worker lifecycle, trusted service identity, session revocation at claim/dispatch, durable outcome handling, and shutdown/backup coordination are reviewed together.
- `SpecialistTaskWorker` is a text-only provider handler today: it does not run shell commands, generated code, or arbitrary workspace writes. That is a useful restriction, not a sandbox.
- Other standalone coordinator constructors remain possible. A future production host must pass the canonical fence and must not share this database with unfenced writers.
- Model/provider output and prior specialist results remain untrusted text. They must never be interpreted as executable commands or authority.
- The in-process Python object graph is not a security sandbox. Never pass privileged stores, session-token providers, handler registries, or approval gateways into model-controlled context.

## Readiness decision

The desktop specialist plan tables now participate in the same profile maintenance fence as workflow-store operations and backup. This closes the identified cooperating-writer race for the native desktop composition. It does **not** establish application-wide quiescence, unify approval ledgers, activate a worker, or authorize consequential host actions.

Tests were authored but not run locally. CI status must be checked against the latest branch head. No merge or deployment.


## Claim-to-provider session-revocation revalidation

The specialist worker now calls `WorkflowRunStore.is_execution_authorized()` after claiming and immediately before invoking its registered handler. The read-only check requires the same running task and worker lease to remain current; for approval-required tasks it also rechecks the approval digest, expiry, approving-session hash, and durable revocation table. A stale, expired, revoked, or no-longer-owned task is failed closed before its task argument is sent to the provider. A regression test simulates logout/revocation after the claim has committed but before the worker's pre-dispatch check and asserts that the handler receives no input.

This narrows, but does not eliminate, the race: the read transaction ends before the provider call begins. A revocation can still commit in that interval, and a synchronous provider request already accepted cannot be reliably recalled by this worker. Holding the profile/SQLite maintenance fence across a network request would block logout and backup and is not an acceptable fix. Therefore this change is a defense-in-depth preflight, not a strict guarantee that logout cancels in-flight or just-about-to-start provider disclosure. Strict cancellation requires a provider/transport cancellation contract plus a separately designed dispatch authorization lease/fencing protocol. The native desktop still does not construct this worker; opt-in local worker execution remains explicit and bounded.

The test is authored but has not been run locally. No CI success, merge, or deployment is claimed.


## Logout-to-worker revocation integration clarification

A follow-up trace confirmed that the native desktop logout path does propagate session revocation into the workflow store: `DesktopCompanionController.logout()` calls `CompanionApplication.revoke_session_tasks()` before `DesktopSessionManager.logout()` revokes the authentication credential. The application delegates to `TaskRuntime.revoke_session_approvals()`, which persists the workflow-store revocation marker before best-effort cancellation of still-queued tasks. The worker's pre-provider `is_execution_authorized()` check reads that same marker. Thus, if an opt-in worker is composed over that same workflow store and approval session identifier, logout after claim but before preflight is detected and provider invocation is blocked.

The regression test now exercises `TaskRuntime.revoke_session_approvals()` rather than writing directly to the store, covering the application-facing revocation path. Desktop currently does not construct a specialist worker, so this is a tested compatibility boundary for a future worker composition, not evidence of active desktop background execution. The separate `build_local_worker_runtime()` creates a process-local ephemeral `approval_session_id`; its lifecycle is distinct from the desktop keyring/authentication session and must not be conflated with desktop logout. Any future UI or service that exposes this local runtime must explicitly invoke `TaskRuntime.revoke_session_approvals()` with that exact approval session when retiring it.

The final check remains a point-in-time preflight, not atomic cancellation of an external provider request. A revocation that commits after the check but before or during transport dispatch cannot be guaranteed to stop that request.


## Opt-in local worker ephemeral-session retirement

- `build_local_worker_runtime()` creates a fresh random `approval_session_id` for each runtime instance. Before this change, the returned runtime had no public shutdown operation that retired that session in the workflow store.
- Added `LocalWorkerRuntime.shutdown(timeout_seconds=30)`. It validates the bounded timeout, durably calls `TaskRuntime.revoke_session_approvals()` for that exact ephemeral session, then asks `WorkerSupervisor.shutdown()` to stop new cycles/claims and drain active cycles.
- Revocation intentionally precedes draining. A task claimed but not yet past its final authorization check is denied; queued tasks approved by that session are cancelled by the existing revocation path. Repeated calls are safe at the revocation-marker level and allow a timed-out drain to be retried.
- If drain returns false, the session remains revoked and the caller must keep the runtime, SQLite store, and provider dependencies alive until a later shutdown retry returns true. Do not close provider/store resources beneath active synchronous handlers.
- This is not forced cancellation: a provider request that crossed the final authorization check before revocation may already be in flight and cannot reliably be recalled. The shutdown call waits for the cycle to unwind within the supplied timeout.
- The local runtime remains explicitly opt-in and creates no threads or automatic cycles. The owner/host must call `LocalWorkerRuntime.shutdown()` as part of its lifecycle; garbage collection or process exit is not a substitute for an orderly drain.
- Added regression coverage for revoke-before-drain ordering, durable revocation after drain timeout, and invalid-timeout rejection before any revocation write. Tests are authored only; no local execution or CI success is claimed.


## Local worker provider and partial-startup ownership

- `build_local_worker_runtime()` borrows the supplied `provider` or `provider_manager`; it does not take ownership. This is intentional because a provider manager can be shared by unrelated application services, and its `close()` method closes both its active and retired provider instances.
- `LocalWorkerRuntime.shutdown()` revokes the ephemeral approval session and drains the supervisor, but does not close borrowed provider dependencies. The caller must keep the provider/provider manager alive while the runtime is active, retry shutdown after a drain timeout, and close the provider dependency only after shutdown returns true and no other service uses it.
- Workflow and specialist coordinator SQLite connections are operation-scoped. The returned runtime does not retain an open SQLite connection between calls. A build failure can leave the private profile directory and initialized schema files, but this factory starts no threads and returns no live runtime on failure.
- The provider manager owns adapters it constructs and has its own idempotent `close()` method. Closing it from this runtime would violate shared ownership. Direct provider instances are likewise treated as borrowed; their owner is responsible for closing them.
- Regression coverage verifies that a successful local runtime shutdown does not close a borrowed direct provider. The runtime factory does not attempt best-effort closure of borrowed dependencies when construction fails; callers retain ownership and must handle their own dependency cleanup.
- This audit does not claim transactional rollback of profile initialization: SQLite schema files may remain after a later provider-handler construction failure. They remain inside the private profile and can be reused or removed by an explicit owner maintenance operation.

## Provider-adapter construction failure ownership

- `build_handlers_from_provider_manager()` may create several routed adapters while composing specialist handlers. A later route or adapter-interface validation can fail after earlier adapters were created.
- The handler factory now records adapters returned by `create_routed_adapter()` and, on a later construction failure, asks the manager's optional `release_routed_adapter(adapter)` hook to release each distinct created adapter in reverse creation order. It deliberately never calls `close()` directly: routed adapters remain manager-owned, and selected adapters are borrowed from the manager.
- Provider-manager implementations that allocate independently owned routed adapters should implement `release_routed_adapter()` as an idempotent, ownership-checked operation. If that hook is absent, this layer cannot safely dispose manager-owned objects; the manager remains responsible for reclaiming partial allocations. This compatibility path is not proof of leak-free cleanup for managers without the hook.
- On successful construction, all adapters remain manager-owned and must outlive the local worker runtime. Drain the runtime before closing the manager. The local worker does not take ownership of, or close, those adapters.
- Regression tests cover a later route failure and an invalid returned adapter, asserting that only the manager's release hook is called. Tests are authored but have not been run locally.

## Serialized local-worker startup and retry semantics

- The local-worker factory now holds the profile's reentrant maintenance fence across construction of the workflow store and specialist coordinator. Their nested store operations use the same fence instance, so cooperating startup/maintenance operations cannot interleave between the two schema-initialization phases.
- Provider-handler construction remains outside that lease. Building routed provider adapters must not hold the profile maintenance lock across potentially provider-specific work.
- SQLite DDL and coordinator registry initialization are idempotent, but the workflow and coordinator constructors do not form one cross-component transaction. If a later phase fails, already-created private schema objects remain; the factory does not delete databases, tables, tasks, or provider state.
- A subsequent startup reopens the same database path and reuses existing records. A regression test injects a provider-handler construction failure, writes an owner task into the initialized database, retries startup, and verifies that the task survives.
- No initialization marker is written: a marker could misleadingly imply that provider adapters or the complete worker are ready. The returned LocalWorkerRuntime remains the only indication that the full in-process composition succeeded; no worker thread or cycle starts during construction.
- The serialization applies only to cooperating processes that use the same profile fence. It does not make the multiple schema steps crash-atomic, protect arbitrary external SQLite writers, or authorize automatic repair. Corrupt/incompatible databases still require explicit owner inspection and backup before repair.
- Regression test is authored but has not been run locally. No CI success is claimed.
