# Phase 304 — Durable reviewer leases

## Scope

Adds a host-controlled SQLite lease authority with signed, task- and
artifact-scoped lease tokens. Claim generations are monotonically increased
when an expired task lease is recovered, fencing tokens from previous
generations.

## Operations

- `acquire`: obtains a lease only when no unexpired lease exists.
- `validate`: verifies token signature, task, artifact, reviewer, generation,
  expiration, and current persisted nonce.
- `renew`: rotates the token nonce and expiry while retaining the generation;
  the previous token immediately becomes invalid.
- `recover`: after expiry, permits a replacement reviewer and increments the
  generation.
- `history`: returns authorized acquisition and renewal events.

## Critical integration limitation

This module is a lease authority, not yet a ledger state-machine integration.
The existing `ReviewerTaskLedger.claim/submit` methods do not yet require a
lease token or generation. Therefore, callers must not treat lease validation
alone as sufficient to prevent a stale worker from submitting to the ledger.
Phase 305 will bind generation fencing into claim and submit transactions,
including recovery authorization and audit events.

The HMAC secret must be supplied from protected host storage. SQLite and the
host remain trusted. Tests are authored but have not been executed.
