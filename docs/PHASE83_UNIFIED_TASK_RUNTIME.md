# Phase 83 — Unified task runtime

## Scope

Phase 83 adds a task-oriented facade over a persistence port and durable append-only event stream. The current implementation uses the SQLite workflow journal as its existing adapter; the official MVP direction is PostgreSQL-backed distributed task truth with SQLite retained for local/test/offline use. This phase records task state and progress; it does not execute arbitrary code or provide an autonomous worker service.

- Idempotent task creation using the existing workflow store.
- Structured task records and existing workflow state/lease semantics.
- Versioned progress and artifact events stored in SQLite.
- Worker lease checks for completion and rejection of expired leases.
- Explicit authorization callback; missing, non-True, or failing authorization fails closed.
- Terminal events for completion, failure, and cancellation.
- Bounded event payloads and optimistic version checks.

## Distributed target architecture

The canonical task envelope includes task ID, tenant/owner, idempotency key, task type/schema version, dependency IDs, state/version, attempt count, lease owner/expiry/fencing generation, deadline, cancellation state, budget, required capabilities, trace/correlation IDs and immutable artifact references. The scheduler claims only dependency-ready work in a transaction. Multiple workers may process independent tasks concurrently, while per-tenant and global quotas prevent resource starvation.

PostgreSQL is the production authoritative adapter for multi-host task state, transactional claims, lease/fencing, uniqueness and recovery scans. SQLite remains a local adapter and must not be used for cross-host leases. Redis is optional dispatch/cache only; messages are hints, not truth. Large outputs belong in content-addressed object storage with authorization/provenance metadata in PostgreSQL. Vector indexes are derived and rebuildable; ACL checks use canonical records. All adapters pass a shared contract suite, and missing production dependencies fail readiness rather than silently falling back.

## Security boundary

The `authorize(actor_id, action, task_id)` callback must be supplied by trusted application code that resolves a real authenticated principal and checks task ownership/tenant access. Never derive actor identity or permissions from task payloads, request bodies, model output, or client-supplied role fields.

This is a service boundary, not HTTP middleware. Hosted authentication transport integration remains separate work.

## Recovery semantics

- Task creation is idempotent by caller-supplied key.
- State updates and event appends use optimistic version checks.
- A worker must own an active lease to complete a task.
- Expired leases must be marked interrupted by the existing store; side-effecting tasks are not automatically replayed.
- Cancellation updates lifecycle state. A future worker/adapter must observe cancellation and safely stop long-running operations.
- Task events record progress and artifacts as metadata; references do not prove an artifact's content or integrity.

## API sketch

```python
store = WorkflowRunStore("state/tasks.sqlite3")
runtime = TaskRuntime(store, authorize=trusted_authorization_callback)
task = runtime.enqueue(
    actor_id=principal.actor_id,
    idempotency_key=request_id,
    name="research-summary",
    steps=[{"name": "collect", "action": "emit", "argument": "collect sources",
            "requires_approval": False}],
)
```

Use `record_progress`, `record_artifact`, `complete`, `fail`, `cancel`, `get`, and `events` to manage lifecycle and metadata. The facade does not execute declared steps itself.

## CI

The Phase 83 workflow runs the new task runtime tests and existing workflow-store/engine regression suites on Ubuntu, Windows, and macOS with Python 3.11, and checks dependency consistency.

## Cross-engine consolidation finding (2026-11-25)

Repository inspection identified a second, independent task persistence engine:
`core/durable_task_engine.py` stores goals, tasks, dependencies, retry metadata, leases and run events directly in SQLite. It is not an adapter of `WorkflowRepository`, and it has no PostgreSQL implementation. In parallel, `automation.task_runtime.TaskRuntime` consumes `WorkflowRepository`, for which both `WorkflowRunStore` (SQLite) and `PostgresWorkflowRunStore` (PostgreSQL) exist.

This means the repository currently has two task lifecycle models and two event models. The presence of a PostgreSQL adapter for `WorkflowRepository` does not make the goal/dependency engine distributed. Deploying both as independent authorities would create split-brain task state, duplicate scheduling semantics, inconsistent lease guarantees and confusing recovery behavior.

### Required convergence direction

- Establish one canonical task/goal lifecycle domain and one authoritative event contract. Goal/dependency planning must be represented in that domain rather than maintained by a second independent queue.
- Preserve existing public APIs through compatibility facades during migration; do not perform a flag-day rewrite or silently move existing task records.
- Make PostgreSQL the authoritative multi-host backend for the distributed deployment. Keep SQLite as a supported single-process/local/offline adapter, not as a second distributed authority.
- Treat Redis as optional notification/dispatch acceleration only. A lost or duplicated notification must not lose or duplicate authoritative task state.
- Preserve dependency readiness, idempotent submission, retry budgets, cancellation, worker leases, fencing generations, tenant ownership and append-only events in the canonical contract.
- For PostgreSQL, claims and all lease-sensitive state/event mutations must be atomic. Worker mutations must be fenced by lease owner, generation and database-clock expiry. A stale worker must not complete, retry or append progress after losing its lease.
- Add a migration/reconciliation plan for existing goal/task records, including stable IDs, idempotency keys, dependency edges, terminal states and event history. Do not dual-write two authorities without a designed reconciliation protocol.
- Run shared semantic contract tests against SQLite and PostgreSQL, plus multi-connection/multi-process PostgreSQL contention tests. Backend-specific SQL tests remain additional coverage, not a substitute for the shared contract.

### Safe implementation sequence

1. Inventory every constructor and caller of `DurableTaskEngine`, `TaskRuntime`, `WorkflowRunStore` and `PostgresWorkflowRunStore`; classify which component currently owns each task type.
2. Define a canonical domain contract that can express both workflow steps and goal/dependency graphs without backend-specific types.
3. Extend the PostgreSQL schema and repository with dependency-aware claims, retry scheduling and goal aggregation under transactional constraints.
4. Implement the equivalent SQLite adapter and shared contract suite.
5. Migrate callers to one facade, retain compatibility shims, and provide an explicit data migration/rollback path.
6. Only then enable multiple distributed workers against PostgreSQL; Redis remains optional and non-authoritative.

### Current implementation status and guardrails

The PostgreSQL `WorkflowRepository` adapter already exists; it uses transactional `SKIP LOCKED` claims, database-clock leases, owner/generation fencing and tenant ownership. The generic contract tests currently demonstrate SQLite structural compatibility, while PostgreSQL has its own opt-in integration suite gated by `YUKI_TEST_POSTGRES_URL`. They are not yet one parameterized shared suite.

The separate `DurableTaskEngine` remains SQLite-only and does not currently expose the same lease-generation fencing contract. It must not be described or deployed as a distributed worker queue. No production multi-host rollout is authorized by this finding. Existing persisted tasks must not be silently re-created, dropped or split across the two stores.
