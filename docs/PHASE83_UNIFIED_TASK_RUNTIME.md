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
