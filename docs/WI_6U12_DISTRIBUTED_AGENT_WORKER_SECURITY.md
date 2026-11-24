# WI-6U12 — Distributed agent-worker security boundary

## Status

**Design and release-gate document only.** This change does not claim that ordinary specialist agent tasks are safe to execute across multiple production replicas. It records the verified persistence boundary and the conditions required before enabling distributed execution.

## Verified repository boundary

The current implementation has two different work-persistence paths:

1. `ProductionSecurityRuntime.work_leases` is a `PostgresSecurityLeaseStore`. It provides atomic PostgreSQL lease claims, random lease tokens, monotonically increasing fencing epochs, database-clock expiry, token/owner/epoch-checked renewal, and conditional release. It is composed for the security operations pipeline.
2. `SpecialistTaskWorker` and `WorkerSupervisor` use `TaskRuntime`. `TaskRuntime` currently requires `WorkflowRunStore`, whose implementation persists tasks and task leases in SQLite. `build_local_worker_runtime` explicitly creates a workspace-local `yuki_tasks.sqlite3` database.

The PostgreSQL security lease store is not passed to the specialist worker, and its lease mutations are not transactionally coupled to `WorkflowRunStore` task state changes. Therefore, its existence does not provide cross-replica fencing for ordinary agent tasks.

## Security consequence

Do not run multiple production replicas against separate SQLite workflow databases and describe the result as a shared durable queue. Do not wrap the SQLite worker in a PostgreSQL lease and assume the operation is fenced: a worker can lose a lease between checking it and committing a task mutation unless the task mutation itself validates the fencing generation in the same database transaction.

The existing local worker composition remains suitable only for its explicitly local trust boundary. The production web runtime's PostgreSQL security composition does not automatically convert local workflow persistence into a distributed task queue.

## Required implementation before production distributed workers

A future implementation PR must introduce a shared PostgreSQL workflow repository and integrate it into task runtime and worker composition. It must not merely add another lease helper.

### 1. Atomic task claim and lease generation

- Persist task state, attempt count, lease owner, random lease token, monotonically increasing lease generation, and expiry in PostgreSQL.
- Claim queued or recoverable work using a short transaction and row locking (for example, `FOR UPDATE SKIP LOCKED`).
- Use PostgreSQL time for lease creation, renewal, expiry, and recovery decisions.
- Return a claim object containing task ID, worker identity, lease token, and generation.
- Ensure every successful reclaim increments the generation; generations never reset when a task is retried.

### 2. Fenced mutations

Every worker mutation—heartbeat, progress, artifact, completion, failure, retry scheduling, and cancellation acknowledgement—must validate task ID, worker identity, token, generation, state, and unexpired lease inside the same transaction that writes the mutation.

A stale worker must be unable to write after another worker has reclaimed the task, even if it continues executing provider work. A preflight check followed by an independent write is insufficient.

### 3. Workload identity and authorization

- Resolve worker principals from trusted workload identity, never task payloads or worker-supplied claims.
- Bind workload credentials to the exact task ID, tenant, agent, audience, lease generation, and narrowly scoped actions.
- Recheck authorization at protected mutation boundaries.
- Enforce delegation revocation atomically with the task mutation where the data model permits; otherwise define and prove a safe consistency protocol.
- Preserve the distinction between the initiating human principal and the executing workload principal.
- Denials and audit failures remain fail-closed.

### 4. Idempotency and external side effects

- Keep task submission idempotency keys bound to a canonical fingerprint of all behavior-affecting options.
- Use a transactional outbox for external effects where possible.
- Derive stable provider idempotency keys from durable task/operation identity, not worker identity or lease token.
- Document that provider acceptance followed by worker crash can cause replay; use provider idempotency or reconciliation to prevent duplicate effects.
- Never claim exactly-once external execution without provider-side support.

### 5. Recovery and shutdown

- Recover expired leases using database time and bounded batches.
- Fence the previous generation before a new worker can mutate task state.
- Persist interruption/retry transitions and event history atomically.
- Stop claiming new work during shutdown, allow bounded in-flight completion, and release or expire claims safely.
- Treat process shutdown timeout as an operational signal, not proof that remote work stopped.

### 6. Compatibility and migration

- Keep the current SQLite `WorkflowRunStore` for local-first use.
- Add an explicit backend-neutral workflow repository contract and a PostgreSQL implementation.
- Avoid silent SQLite fallback in production mode.
- Migrate existing SQLite task history with source integrity checks, stable IDs, idempotency fingerprints, event ordering, and a documented cutover/rollback plan.
- Ensure the production worker host receives the same PostgreSQL workflow repository and `ProductionSecurityRuntime` as a single deployment-owned composition.

## Required acceptance tests

The PostgreSQL integration suite must prove, against a real PostgreSQL service:

1. Two independent worker processes race to claim one task; only one claim succeeds.
2. Expired lease reclamation increments generation and rejects every mutation from the stale generation.
3. A worker that pauses between provider execution and task completion cannot commit after a new generation is issued.
4. Concurrent renew/reclaim operations have one valid serialization outcome and never extend an expired generation.
5. A failed task mutation rolls back its event, state, and lease changes together.
6. Invalid, expired, wrong-audience, wrong-task, wrong-tenant, wrong-agent, wrong-generation, and revoked-delegation credentials are denied.
7. Delegation revocation racing with a protected mutation has a documented serializable outcome.
8. Replayed task submission with the same idempotency key and identical fingerprint returns the original task; changed options conflict.
9. Crash after provider acceptance replays with the same provider idempotency key.
10. Shutdown stops new claims and stale workers cannot mutate after recovery.
11. Multiple replicas share task state, dependencies, attempts, and event history.
12. Database outage during claim, renewal, or mutation fails closed without local fallback.

## Release gate

Distributed production agent workers remain **not approved** until the PostgreSQL workflow repository, transactional fencing, trusted worker credential enforcement, integration tests, migration plan, and deployment lifecycle are implemented and pass on the exact reviewed commit.

The existing PostgreSQL security pipeline lease store remains valid for its current security-pipeline use; this document does not broaden its scope or assert that it is a general-purpose agent task queue.
