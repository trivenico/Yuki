# Phase 142 — Durable agent task journal adapter

## Delivered

- `automation/agent_task_journal.py` adapts the bounded agent loop's lifecycle callback to the existing SQLite-backed `TaskRuntime`.
- Each checkpoint is recorded through the authorized, versioned `task.progress` event API.
- The adapter verifies the task is still running, the expected worker owns the lease, and the lease has not expired.
- Compare-and-swap version tracking prevents this agent from silently overwriting progress from another actor.
- Only a small allowlist of operational counters and error type names is persisted. Prompts, model content, tool arguments, credentials, and tool outputs are excluded.

## Integration

Construct the journal after a worker claims the task, then pass it as the execution loop's `on_event` callback. The task runtime must grant the actor `task:read` and `task:progress` for that task.

## Safety and limitations

- This is a durable lifecycle journal adapter, not a full resumable execution engine.
- It does not persist model messages or intermediate tool outputs.
- It does not renew worker leases. The worker must renew leases through the task store and coordinate version changes; a stale journal fails closed.
- It does not make external tool actions exactly-once. A crash between external side effect and checkpoint remains an ambiguous outcome; do not automatically replay it.
- The caller must still complete/fail/cancel the task through TaskRuntime after the agent loop returns.
- Journal write failures propagate and stop the agent loop.

## Validation

Regression tests cover checkpoint persistence, sensitive-field exclusion, stale versions, worker identity, lease expiry, event allowlisting, and authorization failures. CI on the exact pull request head is required before merge.
