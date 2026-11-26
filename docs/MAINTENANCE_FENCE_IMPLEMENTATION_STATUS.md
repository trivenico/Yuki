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

