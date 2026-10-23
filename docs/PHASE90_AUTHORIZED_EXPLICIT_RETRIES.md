# Phase 90 — authorized explicit task retries

## Scope

This phase adds `TaskRuntime.retry_failed()`. It is a deliberate, authorized operation that creates a new task from a failed or interrupted task's validated definition. It never mutates or replays the terminal source run.

## Controls

- The caller must have `task:retry` permission for the source task.
- The source task is loaded and its expected version checked to prevent stale retries.
- Only `failed` and `interrupted` tasks are eligible. Queued, running, succeeded, and cancelled tasks are rejected.
- A fresh run receives a stable idempotency key derived from the source task and observed version, making duplicate requests idempotent.
- The new task starts with fresh approval state. Any approval-required workflow remains queued until it is approved again for the new run.
- The initial `task.created` event records the source run, source state/version, and approval requirement. The original terminal history remains unchanged.
- No automatic retry, background scheduler, shell execution, or side-effect replay is added.

## Integration contract

The caller must obtain the actor identity from trusted application authentication and authorize retries per task/resource. Do not expose retry capability through a user-facing route until the authenticated identity and authorization adapter is connected. Keep retry permission separate from ordinary task read/create permissions.

## Validation

Dedicated tests cover missing permission, ineligible states, stale versions, duplicate idempotency, source provenance, and reapproval for protected work. Phase 90 CI runs the task runtime tests and workflow safety checks on Python 3.11 across Ubuntu, macOS, and Windows. Merge only after exact-head CI and existing repository gates pass.
