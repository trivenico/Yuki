# Phase 255 — Coordinator-to-provider integrated execution test

## Added integration scenario

The local worker runtime suite now exercises one budget identity from coordinator planning through the actual provider adapter:

1. Submit a specialist plan through the local coordinator.
2. Dispatch it and read the opaque execution metadata from the persisted workflow task.
3. Enqueue additional tasks that deliberately reuse the same logical specialist budget.
4. Execute five calls, matching the local specialist model-turn allowance.
5. Enqueue a sixth call and assert the worker marks it failed before the provider is invoked.
6. Assert provider prompt count and durable budget usage remain at five after rejection.

This verifies that the coordinator, task store, local worker, provider handler, and SQLite budget ledger share the same enforcement boundary.

## Startup-path audit

`main.py` already constructs the local worker runtime and passes its coordinator, task runtime, worker supervisor, and approval session into the optional Textual dashboard. The dashboard supports task creation, plan creation, per-task approval, and explicit worker cycles. The terminal does not currently expose all of those operations directly; the dashboard is the intended operator surface.

The local runtime is constructed at startup, but worker execution is not automatically started. This is intentional: the operator explicitly approves work and starts worker cycles. No background autonomous execution is enabled by this phase.

## Verification

The integration test has been committed but has not been executed in this environment. GitHub Actions workflow-run lookup is not a substitute for local test execution; no passing result is claimed.

## Remaining release gates

- Run focused tests: `python -m unittest tests.test_local_worker_runtime tests.test_specialist_worker tests.test_agent_coordinator tests.test_agent_budget_lifecycle`.
- Exercise the Textual approval and worker-cycle flow on a clean local workspace.
- Verify provider selection and local model readiness on Windows, macOS, and Linux.
- Complete duration accounting and actual tool-call accounting before describing budgets as comprehensive.
- Add supervised, isolated self-upgrade review and rollback validation before enabling self-modification.
