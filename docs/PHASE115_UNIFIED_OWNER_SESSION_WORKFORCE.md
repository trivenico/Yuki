# Phase 115 — Unified owner-session workforce runtime

## Delivered

- Added `build_session_worker_runtime()` to attach a specialist worker to an existing
  `TaskRuntime`. It creates no database, parallel queue, local-operator account, or
  independent approval session.
- The worker, desktop coordinator, and owner-facing task APIs now share the same
  `WorkflowRunStore` instance and therefore the same `tasks.sqlite3` task queue.
- Worker claims are restricted to the SHA-256 hash of the exact owner session that
  approved the task.
- Added a separate `TaskRuntime.internal_authorize` callback for the private worker
  principal. It permits only completion, failure, and expired-lease recovery while
  the exact owner session remains live. It does not grant task listing, creation,
  approval, cancellation, retry, or agent-management permissions.
- The desktop Workforce tab now exposes Start, Pause, Resume, Run one cycle, Stop,
  and Refresh controls.
- Start only opens cycle admission; it does not start a scheduler or execute work.
  Each finite cycle requires the explicit Run one cycle action.
- Cycles execute away from the Qt event thread, with a maximum of three tasks per
  cycle and at most two specialist workers.
- Logout first drains the worker service (30-second bounded wait), then fences
  approvals for the exact owner session, and only then revokes the auth session.
- The desktop lifecycle supports late-bound resource registration and closes the
  worker service before its task runtime/provider dependencies.

## Security and lifecycle invariants

1. Worker composition rejects a missing, forged, expired, or non-current owner
   session before attaching its internal authorization callback.
2. Worker actions are isolated from owner-facing authorization and fail closed if
   session verification fails.
3. A worker cannot claim tasks approved by a different session.
4. Pause closes admission to new cycles while allowing the current finite cycle
   to finish. Stop closes admission and drains; it does not claim to interrupt an
   in-flight provider request.
5. Session revocation fences queued/running work approved by that session. An
   in-flight provider response cannot revive a task that has been cancelled.
6. The worker handlers remain text-only. They do not execute generated code, run
   shell commands, modify files, or perform external side effects.

## Known limitations

- The controls run explicit finite cycles; there is no always-on scheduler,
  autonomous polling, or unattended task execution.
- Stop from the dashboard closes cycle admission immediately and reports whether
  the active cycle is still draining. Logout waits up to 30 seconds and retains
  the live session if draining fails, allowing safe retry.
- A provider request already in flight is not forcibly interrupted.
- The dashboard is local desktop UI; no remote worker control endpoint is added.

## Verification

- Added tests for same-store composition, owner-session validation, worker-only
  authorization, session loss, lifecycle late-bound resource ordering, and worker
  drain before task-session/auth revocation.
- No local test runner was available; tests have not been executed in this session.
- CI status must be checked against the final PR head.
- No worker was started, no merge was performed, and no deployment was made.
