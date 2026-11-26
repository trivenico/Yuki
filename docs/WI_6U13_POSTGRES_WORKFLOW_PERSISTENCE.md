# WI-6U13 — Shared PostgreSQL Workflow Persistence

Status: design and implementation contract  
Depends on: WI-6U12 distributed agent-worker security boundary  
Production status: **not approved** until the implementation, migration, multi-process tests, and deployment gates in this document pass.

## 1. Verified current implementation

The specialist worker path is `automation/specialist_worker.py` → `automation/task_runtime.py` → `automation/workflow_store.py`. The current `WorkflowRunStore` is SQLite-backed and creates both `workflow_runs` and `task_events` in the same SQLite database. It uses `BEGIN IMMEDIATE` for several read/modify/write operations and relies on SQLite transaction serialization.

The task runtime requires more than a simple run table. Its store contract includes idempotent creation, session-bound approval, claim and lease renewal, compare-and-swap transitions, recovery of expired work, atomic state-plus-event writes, event pagination, and session-scoped approval revocation. A PostgreSQL implementation must preserve these behaviors rather than merely implementing `claim_next`.

The existing `version` field is a general task/event optimistic-concurrency version. It advances for progress and audit events as well as state changes. It is therefore **not** a sufficient worker fencing generation: lease ownership needs a separate monotonically increasing generation.

## 2. Required architecture

Introduce a backend-neutral workflow repository contract consumed by `TaskRuntime`. Keep SQLite as the local-development implementation and add a PostgreSQL implementation for shared deployments. Backend selection must be explicit; production mode must never silently fall back to SQLite.

The PostgreSQL repository owns all task persistence in one database:
- workflow definitions, state, results, errors, idempotency digests, approval metadata;
- worker lease owner, expiry, and monotonically increasing lease generation;
- ordered task event journal;
- indexes and constraints enforcing uniqueness and referential integrity.

Do not coordinate PostgreSQL claims with SQLite task writes. Do not use a PostgreSQL advisory lock as a wrapper around a SQLite mutation. The lock, generation check, task mutation, and event append must share one PostgreSQL transaction.

## 3. PostgreSQL schema contract

The first migration must create:

### `workflow_runs`
- `run_id TEXT PRIMARY KEY`
- `idempotency_key TEXT NOT NULL UNIQUE`
- `request_digest TEXT NOT NULL`
- `workflow_name TEXT NOT NULL`
- `definition_json JSONB NOT NULL` (or bounded canonical text if JSONB parity cannot be guaranteed)
- `state TEXT NOT NULL CHECK` against the existing supported states
- `result_json JSONB NULL`
- `error_code TEXT NULL`
- `created_at TIMESTAMPTZ NOT NULL`, `updated_at TIMESTAMPTZ NOT NULL`
- `version BIGINT NOT NULL DEFAULT 0 CHECK (version >= 0)`
- `attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0)`
- `lease_owner TEXT NULL`, `lease_expires_at TIMESTAMPTZ NULL`
- `lease_generation BIGINT NOT NULL DEFAULT 0 CHECK (lease_generation >= 0)`
- approval actor/time/digest/session-hash fields equivalent to the current SQLite schema.

Required indexes: (state, created_at, run_id), (state, lease_expires_at), and (state, approved_session_hash, created_at, run_id). Preserve unique idempotency keys and enforce valid state/lease combinations where practical.

### `task_events`
- `event_id BIGINT GENERATED ... AS IDENTITY PRIMARY KEY`
- `task_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE`
- `sequence BIGINT NOT NULL CHECK (sequence > 0)`
- `kind TEXT NOT NULL`, `actor_id TEXT NOT NULL`
- `payload_json JSONB NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- `UNIQUE(task_id, sequence)`

Use a task-row lock to allocate the next event sequence; do not use an unprotected MAX(sequence)+1 across workers. Event IDs need not match SQLite IDs, but per-task sequence ordering and payload semantics must remain stable.

## 4. Lease fencing protocol

A claim must execute as one transaction:
1. Select an eligible queued run using `FOR UPDATE SKIP LOCKED`, ordered deterministically.
2. Re-check approval eligibility while holding the row lock.
3. Increment `lease_generation`, `version`, and `attempts`; set owner and expiry; transition to running.
4. Append the claim event if the event contract calls for it.
5. Commit and return the new generation with the claimed run.

Every worker-originated progress, artifact, completion, and failure mutation must carry the claimed generation. In the same transaction as the task/event mutation, verify:
- state is running;
- lease owner matches the authenticated worker identity;
- lease generation matches exactly;
- lease expiry is later than the database's current time;
- expected task version matches.

A stale generation must fail with a conflict and make **no** task or event change. Use database time for lease validity to avoid host clock skew. Lease renewal must use the same fencing predicate and must not revive an expired lease. A new claim/recovery increments generation so an old process can never regain write authority.

The worker identity must be derived from the trusted workload identity boundary defined by WI-6U12, not accepted as an unverified request field. The generation is a concurrency fence, not an authentication credential.

## 5. Atomicity and API compatibility

The repository must expose the complete operations used by `TaskRuntime`, including:
- create/get/list and session-filtered queued paging;
- approve-with-event;
- atomic claim and lease renewal;
- append event and bounded event listing;
- transition-with-event;
- expired-lease recovery-with-events;
- any retry/idempotency helpers used by `TaskRuntime`.

Each operation that changes state and writes an event must commit both or neither. Approval revocation must remain restricted to the exact session hash and must not cancel work approved by another session. Preserve current bounded input sizes, redaction, error semantics, and no-automatic-replay behavior.

The backend-neutral contract should be runtime-checkable only if that does not introduce fragile concrete-type coupling. Prefer a `Protocol` and constructor validation of required callable methods over `isinstance(WorkflowRunStore)`.

## 6. Migration and rollout

- Add versioned PostgreSQL migrations; do not create production tables as an implicit constructor side effect.
- Keep SQLite as the local backend.
- Provide an explicit, offline migration/import command with source read-only behavior, stable source snapshot, canonical digest checks, idempotent target handling, and a documented rollback/backup procedure.
- Do not dual-write SQLite and PostgreSQL.
- Require an operator-selected backend and shared PostgreSQL URL for distributed worker mode.
- Refuse distributed mode if any task/event repository is SQLite-backed.
- Close pools on startup failure and graceful shutdown; readiness must verify the task repository.

## 7. Required acceptance tests

1. Two independent PostgreSQL repository instances share task state and events.
2. Concurrent claimers using separate processes/connections never claim one run twice.
3. `SKIP LOCKED` allows workers to claim different queued tasks concurrently.
4. A claim increments lease generation exactly once.
5. A later generation fences every old worker progress, artifact, completion, failure, and renewal attempt.
6. Lease expiry is evaluated using database time and cannot be renewed or completed after expiry.
7. Task mutation and event append roll back together on injected event-write failure.
8. Approval and approval event roll back together.
9. Event sequence allocation remains unique and ordered under concurrent appends.
10. Duplicate idempotency requests return the same run; payload mismatch conflicts.
11. Approval from a revoked session cancels only that session's still-queued tasks.
12. Expired lease recovery appends interruption events atomically and never auto-replays side effects.
13. A PostgreSQL outage fails closed; no SQLite fallback occurs.
14. Migration preserves definitions, state, versions, approvals, results, and event order; corrupted source fails closed.
15. Worker identity is authenticated at the execution boundary and cannot be spoofed by task payload.
16. Pool exhaustion, startup failure, shutdown, and readiness are covered.
17. Existing SQLite workflow/task tests continue to pass unchanged or with backend-neutral fixtures.
18. Multi-process tests run against a real PostgreSQL service in CI; mocks alone are insufficient.

## 11. ASGI production worker lifecycle integration (WI-6U13E)

The ASGI `ConversationRuntime` can now accept an already-constructed `ProductionWorkerRuntime`, but only alongside the exact deployment-owned `ProductionSecurityRuntime`. Both runtimes are checked during ASGI startup; worker readiness is also checked by `GET /health/ready`. Startup fails closed and closes partially owned resources when either readiness check fails.

Shutdown closes the conversation application, then the worker-owned workflow/delegation pools, then the shared security runtime. The worker runtime never closes the security runtime itself. The ASGI host does not execute worker cycles on request handlers or create background threads; a separately supervised worker service must invoke finite cycles.

The web bootstrap requires `YUKI_ENABLE_DISTRIBUTED_WORKERS=true` before accepting an injected worker runtime. Setting the flag without injecting a trusted, fully composed runtime fails startup; injecting a worker runtime without the flag also fails. The flag is off by default. This is an explicit gate, not an assertion that a deployment has completed credential issuer, identity, database migration, or operator readiness requirements.

Tests cover startup readiness, worker dependency failure, readiness endpoint behavior, ownership order, and idempotent close. Exact-head CI is required before treating these tests as passing.

## 10. Production worker composition progress (WI-6U13D)

`automation.production_worker_runtime.create_production_worker_runtime` is an explicit PostgreSQL-only host factory. It requires the deployment-owned `ProductionSecurityRuntime`, trusted actor/worker resolvers, a workload credential verifier, a host-supplied credential provider, fixed worker identity, and explicit specialist handlers. It composes the PostgreSQL workflow repository and PostgreSQL delegation authority, requires their database identity to match the shared security lease database, and enables mandatory workload delegation in `TaskRuntime`.

The returned `ProductionWorkerRuntime` rechecks security, workflow, and delegation readiness before each finite supervisor cycle. It owns and closes only its workflow/delegation pools; the deployment-owned security runtime remains owned by the ASGI host. It does not run migrations, create background threads, or replace the local SQLite bootstrap. Migrations 0007 and 0008 must be applied by the deployment migration role before factory startup.

A fencing correction accompanies this slice: lease generation now remains stable during renewal and advances only when a new claim is issued. This preserves task/delegation-bound credentials through a valid renewal while still fencing a subsequent claim. Tests cover this invariant and fail-closed worker runtime lifecycle behavior; they are not yet confirmed passing in CI.

This factory is an integration building block, not a production rollout approval. A deployment must still provide and validate its trusted credential issuer/KMS signer and identity source, migration role separation, exact-head CI, multi-process crash/replay testing, operational monitoring, and explicit enablement approval.

## 9. Implementation progress (not a production approval)

The implementation is being delivered in dependency-ordered slices:

- **WI-6U13A:** backend-neutral `WorkflowRepository` protocol and structural `TaskRuntime` dependency.
- **WI-6U13B:** `migrations/security/0007_workflow_runs.sql` and `PostgresWorkflowRunStore`, including PostgreSQL row-lock claims, database-clock lease expiry, monotonically increasing lease generations, and atomic state/event writes.
- **WI-6U13C:** `migrations/security/0008_workload_delegations.sql` and `PostgresDelegationAuthority`. Delegation verification locks and validates the ancestor chain using the same PostgreSQL connection supplied by the workflow mutation. Its verification audit event therefore commits or rolls back with the protected task mutation. `TaskRuntime` checks that the workflow repository and delegation authority have the same configured database identity.

These slices add PostgreSQL integration tests and CI coverage, but the code has not been locally executed in the connector-only environment. A queued, absent, or skipped CI run is not a pass. The production ASGI/worker composition, deployment migration/privilege rehearsal, multi-process release evidence, and explicit operator enablement remain outstanding. SQLite remains the local runtime; distributed production workers remain disabled.

## 12. Independent supervised worker service (WI-6U13F)

The standalone entrypoint is `python -m scripts.run_worker`. It requires `YUKI_ENABLE_DISTRIBUTED_WORKERS=true` and `YUKI_WORKER_BOOTSTRAP=module:callable`. The deployment-owned callable must return a fully composed `ProductionWorkerRuntime` whose `security_runtime` is the deployment-owned `ProductionSecurityRuntime`.

The process verifies readiness, executes only finite supervisor cycles, waits between cycles, and handles SIGTERM/SIGINT between cycles. Shutdown closes worker-owned workflow/delegation pools before the shared security runtime. The cycle interval defaults to two seconds and is bounded to 0.1–300 seconds. The bootstrap owns trusted worker identity, specialist handlers, policy, credential issuer/verifier, and database configuration. The entrypoint does not create credentials, run migrations, accept task-payload identity, or start worker threads.

This is an independently supervised process, not an automatic deployment. Workers remain disabled by default; deployment configuration and release gates are still required.

## 8. Release gate

Distributed specialist workers remain disabled until the PostgreSQL repository and worker integration are merged in dependency order, all required PostgreSQL and SQLite CI jobs pass on the exact candidate commit, the migration/restore procedure is rehearsed, trusted workload identity is wired, and an operator explicitly enables distributed mode. This document is a design contract, not evidence that any of those gates have passed.


## 13. Multi-process lease reliability hardening (WI-6U13G)

The PostgreSQL repository now rejects partially supplied worker lease fences. For worker-originated event appends and state transitions, the lease owner and lease generation must be supplied together; generation-only or owner-only calls fail before opening a transaction. This prevents a caller from accidentally invoking a generation-only path that omits the database-clock lease-expiry predicate. Administrative transitions that intentionally do not use a worker lease continue to omit both fields.

The PostgreSQL integration suite now includes:
- Two independent OS processes, each with its own repository instance and connection pool, concurrently claiming two queued tasks; the expected result is distinct task ownership.
- Explicit rejection of owner-only and generation-only event/transition calls, with no task or event mutation.
- Expired-lease recovery followed by a stale worker write attempt; the stale write must conflict, and the recovery event records that automatic replay did not occur.
- Existing independent-connection claim, renewal-generation stability, database-clock expiry, and atomic event/state tests remain in the same PostgreSQL suite.

These are real PostgreSQL integration tests gated by `YUKI_TEST_POSTGRES_URL`, not mock-only tests. The CI workflow already runs this test module against its PostgreSQL 16 service. They have been added but have not been executed in this connector-only session; exact-head CI must pass before this reliability slice is accepted. No production enablement is implied.


## 14. Managed repository lifecycle contract (WI-6U13H)

The backend-neutral WorkflowRepository remains the application-facing persistence contract. It describes task/workflow operations and can be implemented by local SQLite or shared PostgreSQL.

Long-running production hosts require additional capabilities that local repositories do not need. ManagedWorkflowRepository extends the application contract with:
- a stable database_identity, used to reject mismatched workflow, delegation, and security stores;
- check_ready(), which must verify that the durable backend is available and correctly initialized;
- close(), which deterministically releases owned pools/connections.

The production worker runtime now requires this managed contract before accepting the PostgreSQL repository. The SQLite local adapter deliberately remains a plain WorkflowRepository; it is not accidentally treated as a distributed host backend. Integration coverage asserts PostgreSQL satisfies both contracts and SQLite satisfies only the application contract.

This is a capability boundary, not a new database abstraction per agent. All specialists continue to share the same task repository and task lifecycle. The PostgreSQL contract test runs in the existing WI-6U13 PostgreSQL 16 workflow; a test that has not completed successfully is not evidence of readiness.
