# Phase 241 — Apply reasoning budgets at the execution boundary

**Status:** Explicit budget-to-worker adapter added; production runtime call-site wiring and CI verification remain outstanding.

## Repository scope

This work is on `feature/reasoning-context-builder`, not `main`. The default branch has a separate history and contains reverts of earlier branch-targeted changes. Do not merge or cherry-pick this work into `main` until the branch has been reviewed and CI has run.

## Change

- `core/reasoning/execution_budget.py` adds `build_budgeted_execution_loop`.
- The adapter accepts the reasoning budget produced by the advisory task router.
- It validates the four supported numeric limits and rejects unknown fields.
- Each limit is intersected with the existing execution-loop hard default.
- Explicit runtime limits can only reduce the effective budget.
- The adapter still requires the existing `StructuredToolProtocol`; it does not grant tool permissions or bypass approvals.
- Regression tests cover normal propagation, attempted budget escalation, further runtime reduction, and malformed contracts.

## Important limitations

This creates a safe, testable integration boundary, but does not automatically wire every worker or coordinator call site to use it. The approved task service must call this factory when constructing the loop, persist the approved budget with the execution request, and record actual usage. Until that wiring is complete, the router's budget remains advisory for call sites that instantiate `AgentExecutionLoop` directly.

## Fleet scaling

A large registry of specialist roles is not a promise of hundreds of concurrent model processes. The scheduler must separately enforce host-wide and distributed resource limits, model residency, queue fairness, cancellation, and lease ownership.
