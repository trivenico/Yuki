# Phase 247 — Budget Integration Implementation Contract

## Decision

Do not add budget IDs to task definitions until the local specialist worker enforces them. The current execution path is:

`SpecialistAgentCoordinator.advance -> TaskRuntime.enqueue -> WorkflowRunStore -> SpecialistTaskWorker.run_once -> handler(argument) -> provider.generate/generate_for`.

The worker does not call `AgentExecutionLoop`. Therefore wiring only the reasoning loop would leave the local provider-backed specialists unmetered.

## Persistence topology

The local runtime currently passes the same workspace SQLite file (`yuki_tasks.sqlite3`) to `WorkflowRunStore` and `SpecialistAgentCoordinator`. The durable budget store can use this file too, but its current API opens an independent connection and transaction. This is sequentially compatible, not a shared atomic transaction.

The coordinator must use a durable outbox/state machine to bridge the gap:

- `not_required`: legacy explicitly unbudgeted mode only.
- `pending`: plan/subtask persisted; allocation not yet confirmed.
- `allocated`: immutable budget snapshot and parent allocation verified.
- `failed`: allocation failed; dispatch prohibited pending operator/retry policy.

A dispatch reservation must not be created and `TaskRuntime.enqueue` must not be called until the matching subtask state is `allocated`. Reconciliation retries must use deterministic allocation keys and verify the returned snapshot's parent, task ID, policy version, and limits.

## Resource policy

Do not invent a fixed universal resource allocation in the coordinator. Accept a validated budget policy object/provider at construction. The policy must return bounded integer limits for each plan and subtask. Validate every child dimension against its parent through `ExecutionBudgetStore.allocate_child_snapshot`; the store remains the final authority for aggregate allocation capacity.

Use stable identifiers derived from plan/subtask IDs (within the store's 128-character identifier limit). Avoid using mutable descriptions or current routing decisions in budget identity.

## Task payload contract

The worker must receive an explicit metadata object, not parse budget IDs from free-form descriptions:

```json
{
  "name": "subtask-id",
  "action": "emit",
  "argument": "bounded task input",
  "requires_approval": true,
  "execution": {
    "budget_id": "opaque-budget-id",
    "budget_policy_version": "policy-version"
  }
}
```

The worker validates the metadata schema and retrieves the immutable snapshot before calling a handler. Missing or invalid metadata must fail before provider invocation when the runtime is configured for mandatory budgeting.

## Enforcing the current specialist path

The first enforceable budget boundary is the provider handler call:

1. Claim the task and validate the lease.
2. Resolve and validate the specialist and budget snapshot.
3. Reserve one `model_turns` unit and the bounded input character count with deterministic per-task/per-attempt idempotency keys.
4. Only then invoke the provider handler.
5. Record outcome and measured duration.

Do not count a provider call as multiple model turns unless the provider adapter exposes reliable call-level instrumentation. If a handler makes multiple model requests internally, refactor it to use an instrumented provider wrapper or an execution-loop abstraction before claiming accurate per-call accounting.

The current append-only budget store cannot reserve/reconcile in-flight duration safely. For the first integration, enforce a monotonic per-attempt timeout using the specialist's configured timeout and report duration as observed telemetry, not as a durably reserved budget dimension. Mark durable duration enforcement as unsupported until a lease/reservation design exists. Do not reserve the entire maximum duration up front.

Tool-call limits are not meaningful for today's text-only specialist handlers, which have no tool-dispatch interface. Keep the limit in the immutable budget contract, but report it as unused rather than claiming enforcement.

## Required implementation sequence

1. Add a validated budget policy interface and constructor injection to the coordinator.
2. Add additive migrations for plan/subtask budget IDs and allocation state.
3. Add deterministic root-plan and subtask allocation reconciliation.
4. Add child-plan allocations from the delegating parent's subtask budget.
5. Extend `PlannedSubtask` and read APIs to expose only safe budget status, not secret policy internals.
6. Add budget metadata to workflow step definitions only after allocation is confirmed.
7. Add worker-side validation and pre-provider reservations.
8. Wire one shared `ExecutionBudgetStore` into `build_local_worker_runtime`.
9. Require mandatory budgets in the production/local managed worker path; keep unbudgeted standalone mode explicit.
10. Add failure-injection, restart, idempotency, and concurrency tests.

## Acceptance criteria

- No provider invocation occurs if the task has no valid required budget.
- A task's budget snapshot task ID matches the claimed workflow task ID or a documented stable coordinator identity; the binding is validated rather than assumed.
- Root and child allocation retries are idempotent after process restart.
- Parent allocations cannot be overspent under concurrent delegation.
- Pending/failed allocations block enqueue.
- A crash after allocation but before coordinator state update is repaired by reconciliation without allocating twice.
- A crash after task enqueue but before coordinator task binding reuses the same task idempotency key and the same budget ID.
- Worker retries cannot reset cumulative model-turn or transcript usage.
- Provider-call, timeout, and cancellation outcomes are auditable.
- Tests explicitly distinguish enforced dimensions from observed-only dimensions.

## Phase 247 exit status

This contract is an implementation gate, not evidence that integration is complete. The runtime remains unbudgeted until the sequence above is implemented and verified.
