# Phase 308 — Reviewer recovery orchestration

## Scope

Adds a coordinator-facing recovery service that discovers expired in-progress
review tasks, chooses a replacement reviewer, provisions a fresh artifact-
scoped session, and invokes the atomic ledger/lease recovery transition.

## Recovery flow

1. The coordinator asks the ledger for a bounded list of expired in-progress
   tasks. Discovery is artifact-authorized with `review:recover:list`.
2. The orchestrator reads the task's required review capability.
3. It selects the lexicographically first enabled specialist with that
   capability, excluding both the author and previous reviewer.
4. It verifies coordinator orchestration authorization.
5. It issues a new reviewer session before attempting recovery.
6. The ledger atomically abandons and reassigns the task while rotating the
   expired lease to a new generation.
7. If the ledger transition loses a version race or otherwise fails, the
   newly issued session is revoked.
8. On success, the trusted host receives the replacement session and lease
   token in a `ProvisionedReviewerTask`.

## Additional safeguards

- `ReviewerSessionAuthority.revoke_token` lets host code revoke a bearer token
  without exposing session IDs outside the authority.
- Recovered ledger task projections now return the current reviewer identity,
  not the original reviewer embedded in the immutable task payload.
- Discovery is bounded to 1–500 tasks.
- Tasks without an independent enabled replacement are returned as blocked;
  they are not silently reassigned.
- Version conflicts are reported as superseded, not retried blindly.
- Lease tokens and session tokens are returned only to the trusted host caller.
  They must be delivered through a protected worker transport and never added
  to prompts, task evidence, or ordinary logs.

## Boundaries

This service provisions recovery credentials but does not itself execute the
replacement reviewer. The caller must pass the returned task/session/lease to
the existing host-controlled `ReviewerExecutionAdapter`. It does not create a
background polling loop or automatic retry scheduler. Those require an
explicitly configured host lifecycle and operational policy.

## Tests

Added coverage for expired-task discovery, reviewer selection, fresh session
provisioning, lease generation rotation, task projection, and token revocation.
Tests are authored but have not been executed.
