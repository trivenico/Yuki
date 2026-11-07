# Phase 249 — Coordinator budget allocation checkpoint

## Implemented

The coordinator now accepts optional paired dependencies:

- `budget_store: ExecutionBudgetStore`
- `budget_policy(kind, plan_id, subtask_id) -> limits`
- `budget_policy_version`

Supplying only one dependency is rejected. The coordinator creates an `AgentBudgetLifecycle` and additively migrates its existing SQLite tables with:

- `agent_plans.budget_id`
- `agent_plans.budget_allocation_state`
- `agent_plan_subtasks.budget_id`
- `agent_plan_subtasks.budget_allocation_state`

Before `advance()` makes dispatch reservations or enqueues tasks, it calls `ensure_plan_budgets()`. That method:

1. Confirms plan ownership and dispatch authorization.
2. Creates or verifies the deterministic root snapshot.
3. Creates or verifies a deterministic child snapshot for each subtask.
4. Persists each verified budget reference and allocation state.
5. Marks unresolved allocations failed and raises, preventing dispatch.

Allocations are made outside coordinator write transactions because the budget store has its own connection. Deterministic IDs and immutable-snapshot verification make retry after a crash safe. This does not claim cross-store atomicity.

## Important limits — not yet end-to-end enforcement

This checkpoint is intentionally incomplete:

- Budget integration is opt-in. Existing callers that do not inject both dependencies retain legacy unbudgeted behavior.
- The default local runtime factory has not yet been wired to construct and inject the store and policy.
- Workflow step metadata does not yet carry the budget ID.
- The local specialist worker does not yet validate the budget or reserve model-turn/transcript usage before calling the provider.
- Child-team plans currently need a distinct parent-subtask budget allocation path. This checkpoint allocates a root plan budget for each plan and must not be represented as enforcing hierarchical child-plan budgets.
- Durable duration accounting remains unsupported.

Therefore this checkpoint adds coordinator-side allocation and an opt-in pre-dispatch gate, but it does **not** make local specialist provider calls budget-enforced. Do not enable the opt-in coordinator mode in production until worker enforcement and local runtime wiring are complete.

## Verification

The source update was committed, but no test execution or CI run has been observed for this commit. The Phase 248 lifecycle unit tests remain authored but unverified. Next work must add coordinator integration tests and worker enforcement tests before enabling this path by default.
