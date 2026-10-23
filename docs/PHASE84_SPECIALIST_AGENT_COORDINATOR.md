# Phase 84 — Bounded specialist-agent coordinator

## Scope

Phase 84 adds deterministic capability routing and a durable dependency graph for specialist tasks. The coordinator creates task records only when their dependencies have succeeded. It does not call a model, execute arbitrary code, run workers, or bypass the existing workflow engine.

- Static, explicitly configured specialist registry with unique IDs and allow-listed action identifiers.
- Deterministic least-overprovisioned capability matching; disabled agents cannot be selected.
- Bounded plans (maximum 20 subtasks), unique IDs, validated references, cycle detection, and a size limit.
- Idempotent plan submission; reuse of a key with a different plan fails.
- Durable dependency metadata and task IDs in SQLite.
- Dependency-gated dispatch: dependent tasks are not enqueued until every dependency has succeeded.
- Fail-closed authorization for plan, dispatch, and read operations.
- Existing task runtime owns task-level permissions, optimistic versioning, approval gates, and worker lease lifecycle.

## Trust boundaries

The authorization callback must resolve the authenticated principal from trusted application code and enforce resource/tenant access. Never accept actor IDs, capabilities, permissions, agent action names, or approval claims directly from model output or an untrusted request. The registry is configuration, not a dynamic plugin loader.

Each specialist's `action` must be an action already allow-listed by the workflow engine and backed by a reviewed handler. This coordinator only persists and dispatches bounded definitions; the worker/handler layer must still enforce action-specific permissions and validate outputs.

## Dispatch and recovery

Call `submit` to validate and persist a plan, then `advance` to enqueue dependency-ready subtasks. Call `advance` again after workers finish tasks to unlock successors and refresh statuses. Task creation uses stable idempotency keys so a retry after a partial failure does not duplicate a task.

A plan database and the workflow store may be separate files. If separate, dispatch uses stable idempotency keys to recover safely from partial progress; callers should use the same SQLite path for the strongest operational consistency. This phase does not provide a worker scheduler, automatic retries, distributed locks, cancellation propagation, or a hosted API.

## Example

```python
agents = [
    SpecialistAgent("research", "Research", frozenset({"research"}), "emit"),
    SpecialistAgent("writer", "Writer", frozenset({"write"}), "emit"),
]
coordinator = SpecialistAgentCoordinator(
    runtime, "state/yuki.sqlite3", agents,
    authorize=trusted_authorization_callback,
)
plan = coordinator.submit(
    actor_id=principal.actor_id,
    idempotency_key=request_id,
    subtasks=[
        SubtaskSpec("collect", "Collect sources", ("research",), requires_approval=True),
        SubtaskSpec("draft", "Draft the report", ("write",), depends_on=("collect",)),
    ],
)
plan = coordinator.advance(actor_id=principal.actor_id, plan_id=plan.plan_id)
```

The example's `emit` action is illustrative only; use a reviewed, allow-listed action handler appropriate to the application. Approval defaults to required for every subtask.

## Validation

The Phase 84 CI workflow runs coordinator and task runtime regression suites, existing workflow-store and workflow-engine tests, compile checks, and dependency consistency checks on Ubuntu, Windows, and macOS with Python 3.11.
