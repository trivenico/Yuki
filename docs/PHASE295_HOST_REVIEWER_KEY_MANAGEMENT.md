# Phase 295 — Host-controlled reviewer signing keys

## Implemented

Adds `HostReviewerSigningKeyRegistry`, a host-side resolver compatible with
`ReviewerTaskLedger(signer_resolver=registry.resolve)`.

- Signing lookup (`key_id=None`) requires `review:sign` authorization and
  exactly one active, non-revoked key for that reviewer.
- Historical verification lookup requires `review:verify_signature` and the
  exact reviewer/key-ID pair.
- Rotation activates a new key while retaining the prior key for verification.
- Revocation prevents both signing and verification with that key.
- Key IDs are scoped to reviewer identities.
- All authorization errors fail closed.

## Host trust boundary

The registry stores signer objects in host memory. It does not load or persist
secrets. Production startup must retrieve key bytes from an OS credential
store, hardware-backed key store, or KMS and must not expose signer objects or
key bytes to worker prompts, tools, or agent code. The authorization callback
must consult trusted coordinator/host policy, not reviewer-supplied claims.

The registry itself is not wired into the default local runtime in this phase:
that runtime has no authenticated reviewer principal provider or protected
secret-store configuration. Integrators can explicitly inject
`registry.resolve` into the ledger.

## Tests

Five tests cover active key resolution, rotation, revocation, authorization
failure, and duplicate registration. Tests are authored but not executed here.
