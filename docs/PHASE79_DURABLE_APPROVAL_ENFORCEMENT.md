# Phase 79 — durable workflow approval enforcement

## Security invariant

A workflow definition containing any step with `requires_approval: true` must not be claimed or transitioned to `running` until an approval record is persisted for the exact immutable request digest. The approval write is version-checked and only applies while the workflow is queued.

## Implementation

- Add additive SQLite columns for approver attribution, approval time and the approved request digest.
- Apply forward-only schema migration for existing workflow databases.
- Add a versioned approval operation using an immediate SQLite transaction.
- Skip unapproved protected workflows during worker claims.
- Enforce the same gate in lifecycle transitions, so direct state changes cannot bypass claim-time checks.
- Add regression tests for missing approval, explicit approval, stale versions and empty approver IDs.

## Trust boundary

The store records the actor identifier supplied by its caller. It does **not** authenticate the actor, establish permissions, or verify a login session. The API/UI/worker coordinator must authenticate the user and authorize approval before calling the store. Never expose the raw store approval method directly to an untrusted client.

## Safety and limitations

Approvals are tied to the persisted workflow request digest, and workflow definitions are immutable through the store API. A changed definition creates a different request digest and cannot inherit the old approval. Approval expiration, multi-approver policies and signed identity assertions are not part of this phase.

Run the dedicated workflow store tests and the full current-head Windows/macOS/Linux quality gates before merging.
