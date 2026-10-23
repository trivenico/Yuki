# Phase 86 — specialist action contract

## Finding

The durable specialist worker accepts only a single `emit` action because its contract is to pass bounded text to an explicitly registered handler and persist the returned text. The coordinator previously accepted every workflow-engine action (`note`, `wait_for_approval`, and `emit`), even though the specialist worker cannot execute the first two. This could create tasks that are valid to the planner but guaranteed to fail at execution.

## Change

The coordinator now accepts only `emit` for specialist agents. The general workflow engine retains its own action set; the restriction is specific to specialist delegation until the worker implements and tests the other action semantics. Tests reject both unknown actions and currently unsupported but globally allow-listed workflow actions.

## Safety and rollout

- The worker continues to claim only store-approved tasks and relies on existing task-runtime authorization, leases, optimistic versions, and audit events.
- No generated code or shell commands are executed.
- Existing persisted plans are not rewritten. Any old plan using an unsupported action must be reviewed and re-created with a supported `emit` specialist.
- A later phase may add a typed action-handler registry, but each new action must be explicitly implemented in the worker, approved by policy, and covered by regression tests before it becomes routable.

## Validation

The Phase 86 CI workflow runs the coordinator and specialist-worker tests plus task runtime, workflow store, workflow safety, and dependency checks on Ubuntu, Windows, and macOS.
