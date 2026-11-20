# Phase 111 — Worker lifecycle and session revocation

## Implemented

- SpecialistTaskWorker retains the raw process-scoped approval capability only in memory and propagates it to each bounded parallel worker instance. Each worker still hashes the capability before querying the store.
- TaskRuntime.revoke_session_approvals() now requires a currently verified actor/session pair and the dedicated task:session:revoke capability. Caller-supplied session identifiers alone cannot revoke task approvals.
- The workflow store creates a durable revoked_approval_sessions fence table. Session revocation inserts the fence and cancels all matching queued and running tasks in one SQLite write transaction, including the corresponding audit events.
- Approval writes reject fenced session hashes inside their transaction. Worker claims also reject fenced approvals inside the claim transaction. This closes the approve-vs-revoke and claim-vs-revoke ordering gaps across processes.
- A running specialist handler may finish its in-flight provider call after revocation, but the task state is atomically cancelled; the worker checks current task state before publishing completion. Specialist handlers remain text-only/no-side-effect.
- Added tests for queued and running task revocation, durable rejection of later approvals, and propagation of the approval capability into parallel worker instances.
- Updated supervisor tests to supply the required approval capability to all worker instances.

## Concurrency guarantees

SQLite BEGIN IMMEDIATE serializes approval, claim, and revocation write transactions:

1. If approval commits first, revocation fences the session and cancels the queued task.
2. If revocation commits first, a later approval with that session hash is rejected.
3. If claim commits first, revocation can still cancel the running task and clear its lease; completion cannot overwrite the cancelled state.
4. A later worker claim cannot claim a task approved by a fenced session.

This is a task-store fence, not a cross-database atomic transaction with BuiltInAuthService. The caller must invoke task-session revocation while the session is still verifiable, then revoke the authentication session. A future logout coordinator should order these operations and report partial failure rather than silently treating them as one transaction.

## CI findings and verification

- The prior quality-gate run at 7e96a22 had its Ubuntu Python 3.11 unit-test step fail and Windows unit tests cancelled. The source audit identified a concrete constructor propagation defect: run_concurrently() created child workers without the now-required approval_session_id. This has been corrected, and the supervisor test fixtures now provide the required session.
- No local tests were run in this environment. CI for the latest pushed commit must confirm the complete test matrix.
- No merge or deployment was performed.

## Remaining work

- Add a single logout/session-revocation coordinator around BuiltInAuthService and TaskRuntime so callers cannot accidentally revoke authentication first and lose the ability to verify the session.
- Add deterministic multi-thread/process tests that force the approval/revocation and claim/revocation interleavings.
- Define cancellation semantics for any future side-effecting handlers before enabling them; cancellation cannot forcibly undo an external action already in flight.
