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

## 8. Release gate

Distributed specialist workers remain disabled until the PostgreSQL repository and worker integration are merged in dependency order, all required PostgreSQL and SQLite CI jobs pass on the exact candidate commit, the migration/restore procedure is rehearsed, trusted workload identity is wired, and an operator explicitly enables distributed mode. This document is a design contract, not evidence that any of those gates have passed.
