# Phase 277 — Engineering task and audit inspection

The local operator terminal now provides bounded, read-only visibility into durable
engineering task state and audit history:

- `/engineering tasks [STATE]` lists at most 50 redacted task summaries. An optional
  state filter is passed to the runtime's validated state filter.
- `/engineering task-events TASK_ID` reads at most 50 events for one task after
  task-level authorization. It prints event metadata, not event payloads.
- `/engineering plan-events PLAN_ID` reads at most 50 coordinator events after
  plan ownership and authorization checks. It prints sequence, timestamp, and kind,
  avoiding payloads that may contain operational context.

These commands do not dispatch work, approve tasks, or run workers. Existing
`show`, `advance`, `approve`, `cycle`, and `cancel` commands remain separate.

## Security and operational notes

- Task listing uses the runtime's dedicated `task:list` permission and returns
  redacted `TaskSummary` records.
- Task event reads use `task:read`; plan events use coordinator `agent:read`.
- Output is bounded to 50 records per command.
- Errors are reported by exception type, not raw exception messages.
- This phase does not add background workers or autonomous dispatch.

## Validation

The implementation was committed to the feature branch. Tests were not executed
in this environment; verify with the repository's CI and local test suite.
