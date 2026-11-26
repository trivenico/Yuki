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

### 1. Approval has no expiry or live-session check at claim time

The persisted approval currently consists of actor, timestamp, session hash, and request digest. Claim validates the presence of these fields and checks the local revocation-marker table, but it does not enforce an approval expiry or consult the authentication authority for current session validity. A session that expires naturally, is invalidated through a path that does not write the workflow revocation marker, or is revoked outside the coordinated desktop logout flow can leave approval metadata consumable.

**Required remediation:** make approval a bounded capability with an explicit expiry, policy revision, exact request digest, actor, and session correlation. Claim must validate expiry and the current revocation generation from a trusted durable authority. If auth and workflow data remain in separate databases, define a transactional outbox/reconciliation contract and fail closed during uncertainty. A session hash alone is not proof that a session remains active.

### 2. Retry source-version check is now atomic

Implemented on this branch in `automation/task_retry.py`, called by `TaskRuntime.retry_failed()`. The store operation uses one `BEGIN IMMEDIATE` transaction to re-read the source row, verify its exact version and failed/interrupted state, derive the idempotency key and definition from that locked row, and create the child plus its `task.created` provenance event atomically.

Repeated requests for the same source version return the same child only when the stored request digest matches. A stale source version conflicts before child creation. The new child has no inherited approval fields, so approval-required work remains blocked until freshly approved.

Regression tests were authored for concurrent retries returning one child and for rollback when insertion of the retry audit event fails. These tests have **not** been run locally; CI status must be checked before treating the change as verified.

### 3. Approval consumption is currently reusable metadata

Claim changes the task from queued to running, which prevents a second ordinary claim of that same row. However, approval metadata remains in the row and is not represented as a separately consumed capability with its own immutable record/consumption event. The task state is the current single-use guard. Future transitions back to queued or new recovery logic must not accidentally make old approval reusable.

**Required remediation:** preserve the current no-in-place-replay rule. Any future requeue/recovery path must clear approval fields and require a new approval, or use a separate approval record with an atomic consumed state and attempt binding. A retry must always create a new approval scope.

## Required tests before enabling specialist execution

- Approval expiry immediately before claim and at the claim transaction boundary.
- Session expiry and revocation through every supported logout/revocation path.
- Claim while auth authority is unavailable or revocation state is stale: must fail closed.
- Retry racing with source cancellation/state transition: transaction serialization must reject stale source versions.
- Concurrent retry requests for one source version return one child run.
- Source version changes before retry transaction: no child created from stale state (enforced by the in-transaction version check).
- Retry of approval-required work has no inherited approval and cannot be claimed before fresh approval.
- Any future requeue/recovery path cannot reuse old approval metadata.

## Readiness decision

Keep specialist execution disabled for consequential actions. Atomic retry creation and fresh approval state are now implemented, with regression tests authored but awaiting CI. Approval expiry/live-session revocation at claim remains open, as does CI verification of this increment.
