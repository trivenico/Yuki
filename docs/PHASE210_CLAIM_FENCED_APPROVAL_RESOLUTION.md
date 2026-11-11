# Claim-fenced approval credential resolution

## Scope

`LeaseBoundApprovalResolver` composes a worker-bound, one-shot approval
resolver with the durable task engine's per-claim token. It validates the
exact task/worker/token before credential lookup and again after lookup. If
the claim expires or is reassigned during lookup, the credential is discarded.
Because the underlying broker may already have consumed its one-shot record,
the owner must issue a fresh approval.

The adapter also creates a fail-closed callback suitable for the final local
skill-handler execution guard.

## Security properties

- Missing, malformed, stale, reassigned, or unverifiable claim tokens fail
  closed.
- Resolver identity pinned to another worker is rejected at construction.
- Credential lookup must complete under the same live claim at both checks.
- A broker/storage validation exception never grants access.
- Credentials are returned only in memory and are not logged or persisted by
  this adapter.

## Explicit limitations

This is not an atomic transaction spanning SQLite and an external provider.
The lease can change immediately after the post-resolution check. The final
local execution guard narrows that window but cannot make a remote API commit
atomic with the local task database. Provider adapters must independently
enforce generation-aware credentials at their own commit boundary and use
provider idempotency/outbox semantics where available. Already in-flight
external effects cannot generally be recalled.

This module is a composable security primitive, not a claim that the approval
broker is fully integrated into every Yuki worker or tool adapter. Production
composition still requires a real KMS-backed AEAD implementation, key
rotation/custody, durable audit, and end-to-end worker integration.
