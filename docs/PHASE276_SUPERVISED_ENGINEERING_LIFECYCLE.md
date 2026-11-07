# Phase 276 — Supervised engineering lifecycle

## Operator commands

The guarded terminal exposes the existing coordinator and worker supervisor through
explicit local operator commands:

- `/engineering show PLAN_ID` reads an owner-authorized plan and its subtask/task bindings.
- `/engineering advance PLAN_ID` requires a fresh high-risk confirmation and creates
  dependency-ready durable workflow tasks. It does not execute worker handlers.
- `/engineering approve TASK_ID` reads the task, permits approval only while it is
  queued, requests confirmation, and records approval against the exact definition
  digest and this process's approval session.
- `/engineering cycle` requires a fresh high-risk confirmation and runs exactly one
  configured bounded supervisor cycle.
- `/engineering cancel PLAN_ID` requires confirmation and delegates cancellation
  to the coordinator/runtime so task state changes remain audited.

## Safety properties

- No background scheduler or autonomous dispatch was added.
- Plan advancement and worker execution are separate operator actions.
- Approval is session-bound and definition-digest-bound by the workflow store.
- The worker cycle uses the existing configured task and worker concurrency limits.
- Exceptions are reported by type; sensitive exception text is not printed.
- The cycle can invoke the configured provider for approved specialist work, which
  is why the terminal asks for a separate confirmation immediately before it runs.
- Verification execution remains disabled unless an explicit trusted command
  allowlist and verification workspace are configured. Git-backed workspace
  capture is not silently enabled by terminal startup.

## Operational sequence

1. Use `/engineering agents` to inspect registered specialists.
2. Use `/engineering plan ROLE | DESCRIPTION` to persist an approval-required plan.
3. Use `/engineering show PLAN_ID` to inspect the durable plan.
4. Use `/engineering advance PLAN_ID` to create ready workflow tasks.
5. Approve each queued task with `/engineering approve TASK_ID`.
6. Run `/engineering cycle` to execute one bounded batch.
7. Inspect the plan again; repeat advance/approve/cycle deliberately as dependencies
   become ready. Use `/engineering cancel PLAN_ID` to stop outstanding plan work.

## Validation status

The terminal integration was committed, but automated tests were not run in this
environment. CI status must be checked independently; a missing status report is
not evidence that tests passed.
