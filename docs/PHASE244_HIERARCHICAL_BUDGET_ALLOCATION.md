# Phase 244 — Hierarchical budget allocation

**Status:** Atomic child allocation is implemented in the durable budget store. Coordinator integration and CI remain outstanding.

## Contract

Child snapshots must be created through `allocate_child_snapshot`. The legacy direct parent reference on `create_snapshot` now fails closed to prevent an unreserved child budget.

The allocator runs under SQLite `BEGIN IMMEDIATE`, checks every resource dimension, counts direct parent spend plus all previously reserved child allocations, and inserts the child snapshot and allocation record in the same transaction. Allocation keys are idempotent; replay with different child identity or limits is rejected.

Parent reservations also count existing child allocations against the parent ceiling. This ensures that parent direct work cannot consume capacity already delegated to child teams.

## Important limitations

- Allocations are reserved in full at child creation; unused child allowance is not yet reclaimed.
- The coordinator does not yet create these snapshots as part of its child-plan transaction.
- The parent-child budget and coordinator plan databases are not one atomic transaction.
- Every worker must receive its child's budget ID and use the same shared durable database.
- CI has not yet been run.

These limitations are intentional and explicit; do not treat hierarchy as production-complete until coordinator integration and recovery tests are done.
