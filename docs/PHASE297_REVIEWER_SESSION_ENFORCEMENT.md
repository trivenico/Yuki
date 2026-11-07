# Phase 297 — Reviewer session enforcement in the ledger

## Implemented

ReviewerTaskLedger now requires a ReviewerSessionAuthority instance at
construction. The ledger refuses to claim or submit a review unless the caller
provides a valid, unexpired, non-revoked token and a unique operation ID.

- Claim requires `review:execute`.
- Submission requires `review:submit`.
- Session identity must equal the assigned reviewer identity.
- Session artifact digest must equal the task's exact artifact digest.
- The host authorization callback is rechecked at operation time.
- Session operation IDs are consumed transactionally and cannot be replayed.
- Session records are cross-checked against token reviewer, artifact, and expiry claims.
- The verification bridge continues to require ledger-verified result signatures.

Assignment and read operations retain their separate coordinator/auditor
authorization callback; they do not consume reviewer execution sessions.

## API change

ReviewerTaskLedger construction now requires
`session_authority=ReviewerSessionAuthority(...)`.
Both `claim(...)` and `submit(...)` now require `session_token` and
`operation_id`. Callers must mint a fresh unique operation ID for each
attempt. Reusing an operation ID is rejected, including retries; callers should
retry with a new operation ID and rely on the ledger's state/result idempotency
checks to prevent duplicate state transitions. This intentionally favors
replay rejection over transparent transport-level retries.

## Security boundary and remaining integration

The token is a bearer credential. A trusted host transport must deliver it
directly to the worker without putting it in prompts, logs, repository files,
or shared task payloads. The host must load the session secret from protected
storage. The reviewer ID supplied to the ledger is not sufficient by itself:
it must match the authenticated session and pass the trusted authorization
callbacks.

The default local runtime and coordinator are not yet wired to create these
sessions. No reviewer work is autonomously dispatched by this phase. The
ledger/bridge tests use explicit test secrets and authorization callbacks.

## Tests

Ledger and verification-bridge fixtures now construct session authorities
and pass scoped tokens for reviewer claim/submission. Added a ledger-level
replay rejection test. Tests are authored but not executed in this environment.
