# Task retry and approval-lifetime audit

**Review date:** 2026-11-26  
**Branch:** `research/reference-projects-integration-assessment`  
**Scope:** Source review of `automation/task_runtime.py` and `automation/workflow_store.py`. No local tests were run.

## Controls confirmed

- Approval is bound to the queued task's immutable request digest and expected version.
- The claim path requires approval metadata for approval-required definitions and rejects approval sessions whose SHA-256 hash is in the durable revocation table.
- Retry creates a distinct workflow run with a deterministic idempotency key derived from the original task ID and observed version. It copies the original definition, but the new row starts with no approval metadata. Approval-required work therefore needs a new approval.
- Retry is restricted to failed or interrupted tasks and requires the caller's `task:retry` authorization.
- The workflow definition has no general-purpose update method in the inspected store, reducing the risk of changing the approved definition in place.

## Remaining findings

### 1. Approval expiry is enforced; live-session validity remains open

Implemented on this branch:
- New `approved_expires_at` column, including an additive schema migration.
- Every newly recorded approval receives a fixed ten-minute lifetime.
- Both the direct and evented approval paths persist the expiry.
- Worker claim and both direct/evented transitions to `running` reject missing or expired approval expiries inside the workflow database write transaction.
- Legacy approval rows without an expiry fail closed and cannot be claimed.
- Regression tests cover expired direct approvals, evented approvals, and the direct transition path.

The expiry is a bounded capability lifetime, **not** proof that the underlying authentication session is still active. The workflow store still checks its durable local revocation marker, but does not consult the authentication authority at claim time. A session that expires naturally or is revoked through a path that does not write the workflow marker may remain usable until the ten-minute approval expires.

**Remaining remediation:** bind approval to a trusted session validity/revocation generation. If auth and workflow data remain in separate databases, define a transactional outbox/reconciliation contract and fail closed during uncertainty. Do not describe the local session hash as live-session verification.

**Concrete authority-boundary audit (2026-11-26):**

- `BuiltInAuthService` persists opaque session-token hashes, user IDs, creation/expiry timestamps, and `revoked_at` in `auth_sessions`. `resolve_session()` checks token hash, expiry, revocation, and disabled-user state against that database.
- `WorkflowRunStore` persists only the SHA-256 hash of the approving session in `workflow_runs.approved_session_hash` and its own `revoked_approval_sessions` table. It does not have a foreign key or transaction shared with `auth_sessions`.
- `BuiltInAuthService.revoke_session()` updates only `auth_sessions`. `disable_user()` disables the account and revokes its auth sessions in that database, but does not itself write workflow revocation markers.
- The desktop logout/task cleanup path can write the workflow marker before revoking its session, but that is a coordinated caller path, not an invariant enforced by the authentication authority. Other callers of the auth service can bypass that coordination.
- Consequently, querying `resolve_session()` immediately before `claim_next()` would narrow the race but would not make the check atomic: a revocation can commit in the auth database between the live-session check and the workflow claim transaction. Two independent SQLite transactions cannot provide this cross-database serialization.

**Required architecture before specialist worker enablement:**

1. Choose one authoritative transaction boundary for session revocation and approval consumption. Preferred options are (a) co-locate session validity and workflow claim state in one database/transaction, or (b) introduce a durable auth revocation outbox plus a workflow-side high-watermark/reconciliation protocol with a defined fail-closed freshness bound. A best-effort callback alone is insufficient.
2. Route every session revocation and user-disable operation through the same authority contract. No direct auth-store mutation may silently skip workflow invalidation.
3. Bind approval to immutable session identity plus the authoritative revocation epoch/generation, not just a bearer-token hash. Never persist raw session tokens in workflow rows, events, logs, or IPC.
4. Make worker claim reject approvals when the authority is unavailable, its revocation cursor is stale, the generation mismatches, or the session is expired/revoked/disabled.
5. Define in-flight semantics separately: a revocation that races after a task has been claimed cannot be assumed to stop an already-running handler. Require cooperative cancellation plus a target-side fencing/authorization check immediately before every consequential external side effect.
6. Test races across independent processes and database connections: revoke-vs-claim, disable-user-vs-claim, authority outage, stale outbox cursor, process crash between outbox commit and projection, replay after restart, and claim-before-revocation behavior.

Until this is implemented, the ten-minute expiry is only a maximum approval age. The existing workflow marker blocks only revocations that were explicitly projected into the workflow database.

### 2. Retry source-version check is now atomic

Implemented on this branch in `automation/task_retry.py`, called by `TaskRuntime.retry_failed()`. The store operation uses one `BEGIN IMMEDIATE` transaction to re-read the source row, verify its exact version and failed/interrupted state, derive the idempotency key and definition from that locked row, and create the child plus its `task.created` provenance event atomically.

Repeated requests for the same source version return the same child only when the stored request digest matches. A stale source version conflicts before child creation. The new child has no inherited approval fields, so approval-required work remains blocked until freshly approved.

Regression tests were authored for concurrent retries returning one child and for rollback when insertion of the retry audit event fails. These tests have **not** been run locally; CI status must be checked before treating the change as verified.

### 3. Approval consumption is currently reusable metadata

Claim changes the task from queued to running, which prevents a second ordinary claim of that same row. However, approval metadata remains in the row and is not represented as a separately consumed capability with its own immutable record/consumption event. The task state is the current single-use guard. Future transitions back to queued or new recovery logic must not accidentally make old approval reusable.

**Required remediation:** preserve the current no-in-place-replay rule. Any future requeue/recovery path must clear approval fields and require a new approval, or use a separate approval record with an atomic consumed state and attempt binding. A retry must always create a new approval scope.

## Required tests before enabling specialist execution

- Approval expiry immediately before claim and at the claim transaction boundary (regression coverage authored; CI pending).
- Session expiry and revocation through every supported logout/revocation path.
- Claim while auth authority is unavailable or revocation state is stale: must fail closed.
- Retry racing with source cancellation/state transition: transaction serialization must reject stale source versions.
- Concurrent retry requests for one source version return one child run.
- Source version changes before retry transaction: no child created from stale state (enforced by the in-transaction version check).
- Retry of approval-required work has no inherited approval and cannot be claimed before fresh approval.
- Any future requeue/recovery path cannot reuse old approval metadata.

## Readiness decision

Keep specialist execution disabled for consequential actions. Atomic retry creation and ten-minute approval expiry are implemented, with regression tests authored but awaiting CI. Live-session validity/revocation at claim remains open, as does CI verification of this increment.
