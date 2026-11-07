# Phase 250 — Local specialist budget enforcement

## Implemented

This phase connects the coordinator's budget allocation to the actual local provider-handler boundary.

### Task metadata

- `AgentBudgetLifecycle.subtask_task_identity()` exposes the stable logical identity used by the immutable subtask snapshot.
- The coordinator adds a dedicated `execution` metadata object to each budgeted workflow step:
  - `budget_id`
  - `task_identity`
  - `policy_version`
- The workflow store validates this exact metadata schema and persists it in the canonical workflow definition. The metadata is not appended to the specialist prompt.

### Worker enforcement

When a worker is configured with an `ExecutionBudgetStore`, it requires the metadata object, retrieves and integrity-checks the immutable snapshot, and validates its logical task identity, parent relationship, and policy version before calling the handler.

Before invoking the provider-backed handler, it durably reserves:

- one `model_turns` unit
- the specialist input's character count in `transcript_chars`

Reservation keys are stable for a workflow task attempt. Failed or interrupted attempts do not silently reset cumulative usage. A reservation is intentionally made before provider invocation, so a crash after reservation conservatively consumes allowance rather than risking unmetered replay.

The worker pool propagates the same budget store to its per-thread worker instances.

### Local runtime

`build_local_worker_runtime()` now creates one `ExecutionBudgetStore` on the same SQLite database path and injects that instance into both coordinator and specialist worker.

The local policy is explicit and versioned as `local-v1`:
- Plan: 100 model turns, 20 tool calls, 1,200,000 ms duration allocation, 200,000 input characters.
- Each subtask: 5 model turns, 1 tool call, 60,000 ms duration allocation, 10,000 input characters.

The store atomically bounds the sum of child allocations against the plan snapshot. These values are initial conservative defaults, not hardware-adaptive policy.

## Tests authored

- Worker test verifies reservations exist before successful handler execution and usage is persisted.
- Worker tampering test verifies a mismatched logical task identity fails before handler invocation and consumes no model turn.
- Local runtime test verifies the shared store is injected, metadata survives workflow persistence, and local execution records usage.

No test runner or repository checkout is available in this execution environment. GitHub reported no combined status checks for the latest test commit. These tests are committed but have **not** been executed or verified as passing.

## Explicit limitations

- Duration is allocated but not durably enforced at runtime. The current budget ledger lacks a safe lease-based duration reservation model.
- Tool calls are allocated but not consumed because the current specialist handler interface has no tool-dispatch boundary.
- Model turns are counted as one provider-handler invocation. An adapter that internally fans out to multiple model calls needs provider-level instrumentation before exact call accounting can be claimed.
- Transcript accounting measures the raw specialist input string length, not the final provider prompt including system instructions or provider-side transformations.
- Budget integration is mandatory in the local managed runtime, but direct/legacy standalone worker configurations remain backwards compatible when no budget store is supplied.
- Child-team plans still require allocation of their root budget from the delegating parent's subtask budget; they are not yet fully hierarchical.
- The workflow store and budget store use separate SQLite connections. Durable IDs and retry reconciliation reduce crash risk, but there is no single cross-store transaction.

## Phase 250 exit status

The local coordinator-to-workflow-to-worker path now carries and enforces durable model-turn and input-character budgets before provider calls. This is meaningful end-to-end progress, but it is not a production readiness claim. CI/test verification, child-plan hierarchy, duration leases, tool instrumentation, and recovery/failure-injection coverage remain open.
