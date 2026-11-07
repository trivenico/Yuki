# Phase 246 — Coordinator/Worker Budget Integration Audit

## Status

Audit complete; runtime integration is **not implemented** in this phase. No task-dispatch or worker-execution behavior is changed by this document.

Inspected on branch `feature/reasoning-context-builder`:

- `automation/agent_coordinator.py`
- `automation/task_runtime.py`
- `automation/local_worker_runtime.py`
- `automation/specialist_worker.py`
- `automation/worker_supervisor.py`
- `core/agent_execution_loop.py`
- `core/reasoning/execution_budget_store.py`
- `tests/test_agent_coordinator.py`

## Confirmed execution path

1. `SpecialistAgentCoordinator.advance()` validates a plan/subtask, reserves specialist concurrency, and calls `TaskRuntime.enqueue()`.
2. `TaskRuntime.enqueue()` persists the workflow definition as a list of step dictionaries.
3. `WorkerSupervisor` invokes `SpecialistTaskWorker.run_once()`.
4. `SpecialistTaskWorker.run_once()` claims the task, extracts the specialist ID from the workflow name, reads the first step's `argument`, and calls a registered handler.
5. The local runtime factory builds provider-backed handlers with `build_provider_specialist_handlers()`. Those handlers call `provider.generate()` (or `generate_for()`) directly.

## Critical architectural finding

The durable budget reservations added to `core/agent_execution_loop.py` do **not** currently protect the local specialist worker path. The worker does not construct or call `AgentExecutionLoop`; it invokes a provider handler directly. Adding a `budget_id` field to the workflow step alone would therefore be metadata propagation, not enforcement.

The coordinator currently has no `ExecutionBudgetStore` dependency, no budget columns on plans/subtasks, and no budget allocation in `submit()` or `submit_child_plan()`. Its task enqueue step contains no budget reference.

## Required integration design

### 1. Canonical budget contract

Use the store's canonical resource names and limits:

- `model_turns` / `max_model_turns`
- `tool_calls` / `max_tool_calls`
- `duration_ms` / `max_duration_ms`
- `transcript_chars` / `max_transcript_chars`

The reasoning adapter's `max_duration_seconds` must be converted explicitly to integer milliseconds. Never silently compare seconds with milliseconds.

### 2. Durable plan ownership

Add nullable, additive-migration columns for `budget_id` on plans and subtasks. Extend the plan read model only after the migration and allocation lifecycle are implemented.

Root plans need a root snapshot. Each subtask needs a child snapshot allocated from the plan snapshot. A delegated child plan must itself be allocated from the source parent's subtask budget, and its subtasks must be allocated from that child-plan budget.

Allocation IDs and idempotency keys must be deterministic from persisted plan/subtask identity, not generated anew on retry.

### 3. Cross-store crash safety

The coordinator and budget store each open their own SQLite connections. Even if configured with the same file, the current APIs do not expose a shared transaction. If they use different files, there is an unavoidable crash window between coordinator persistence and budget allocation.

Do not claim cross-database atomicity. Introduce a durable allocation state/outbox (for example, `pending`, `allocated`, `failed`) and block dispatch unless every required allocation is confirmed. Reconciliation must be idempotent and fail closed.

### 4. Task and worker propagation

Only after an allocation is confirmed:

- Put the opaque `budget_id` in a dedicated, validated workflow-step metadata field.
- Have the worker read it from the claimed persisted definition.
- Validate that the referenced snapshot exists and belongs to the claimed task.
- Reserve model-call and transcript usage before invoking the provider.
- Enforce tool-call limits at the tool-dispatch boundary if/when specialist handlers gain tool execution.
- Enforce duration with a monotonic deadline and a documented durable accounting strategy.

The current specialist handlers are text-only provider calls, not tool-using agent loops. Do not pretend their execution is protected by the existing `AgentExecutionLoop` reservations.

### 5. Production wiring

The local runtime factory must construct one shared `ExecutionBudgetStore` for coordinator and worker use, backed by a durable workspace database. Production worker construction must not silently fall back to an unbudgeted execution path once budget enforcement is enabled.

Keep standalone/test construction explicit and compatible until all call sites and migrations are updated.

## Duration-accounting caveat

The current budget store has append-only positive reservations and no lease/refund/reconciliation operation for duration. Reserving the full maximum duration at worker start would consume the entire allowance even for short tasks; charging only after completion would permit concurrent workers to overshoot.

A correct implementation needs an explicit in-flight duration reservation/lease model or a deliberately conservative policy. Phase 246 must not claim durable duration enforcement until that is designed and tested.

## Required tests before declaring integration complete

- Root plan allocation is idempotent across retries and process restart.
- Every subtask allocation is bounded by its parent allocation.
- Child-plan allocation is bounded by the delegating subtask allocation.
- Concurrent child allocations cannot overspend a parent.
- Allocation failure or pending state prevents task enqueue.
- Duplicate dispatch reuses the same task and budget references.
- Worker rejects missing, unknown, mismatched, or tampered budget metadata before provider invocation.
- Worker reserves usage before provider invocation; exhaustion prevents the provider call.
- Restart and recovery do not mint a fresh budget or reset cumulative usage.
- Duration behavior is tested with a fake monotonic clock and concurrent workers.

## Phase exit criteria

Phase 246 is complete only when the coordinator allocation lifecycle, task metadata propagation, worker-side enforcement, local runtime wiring, migrations, and tests are implemented together. A documentation-only audit is not a completion claim.

