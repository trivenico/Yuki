# Phase 251 — Hierarchical delegated-plan budgets

## Implemented

- Added deterministic delegated-plan budget identities derived from the parent plan, source subtask, and child plan edge.
- Delegated plan snapshots are allocated with the budget store's atomic child-allocation operation. They cannot be silently re-parented.
- Coordinator reconciliation now detects hierarchy links and allocates a child plan from its delegating subtask budget rather than creating a new root budget.
- Parent plan budgets are reconciled before child allocations, and the child plan's subtasks receive bounded shares of that child plan envelope.
- Added lifecycle tests for parent linkage, retry idempotency, and attempted re-parenting.

## Safety properties

- Allocation happens before dispatch; a missing or inconsistent parent allocation fails closed.
- The immutable snapshot and allocation key are deterministic, so restart retries reuse the same child allocation.
- A delegated plan cannot allocate more than the source subtask's remaining envelope.
- Subtask allocations are divided across the child plan's declared subtasks before applying stricter per-agent policy limits.

## Remaining work

- Coordinator-level integration tests must exercise actual delegation, restart, and dispatch.
- The SQLite coordinator database and budget store still use separate connections. Reconciliation is retry-safe but not one cross-store transaction.
- Runtime duration enforcement and tool-call accounting remain incomplete.
- The current local runtime still bounds active worker concurrency; this work does not imply hundreds of local models run simultaneously.

## Verification

Tests were added but have not been executed in this environment. No CI result is claimed.
