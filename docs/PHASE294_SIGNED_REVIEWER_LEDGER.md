# Phase 294 — Signed reviewer ledger integration

## Implemented

- ReviewerTaskLedger now requires a trusted signer resolver at construction.
- Submission resolves the reviewer's active signer and signs the canonical structured result payload.
- Signature envelope (key ID, algorithm, HMAC-SHA256) is persisted alongside the result.
- Every read recomputes the result digest, reconstructs and validates the result, resolves the historical key by reviewer and key ID, and verifies the signature.
- The verification bridge refuses results unless the ledger confirms signature verification.
- Existing databases receive a nullable signature column migration. Historical submitted rows without signatures fail closed and must be re-reviewed or migrated through a separately trusted process.

## Key rotation

The resolver contract is `signer_resolver(reviewer_agent_id, key_id)`. Pass `None` as key ID to obtain the active signing key for new submissions. For reads, the ledger passes the stored key ID; the resolver should return the corresponding retained verification signer. Retired keys must remain available for historical verification for the required retention period. Unknown or revoked keys fail closed.

## Security boundaries

HMAC proves possession of a shared secret, not a non-repudiable public identity. The host must isolate keys from reviewer processes and the SQLite database, authorize signer use by reviewer identity, and protect backups. If reviewer code itself can freely call the signer, it can sign fabricated results; production deployments should put signing behind a trusted host-side service after authenticated worker completion. HMAC key compromise permits forgery for that key.

## Tests

Updated ledger and bridge tests to configure trusted signer resolution and assert signed-result verification. Added invalid-signature rejection coverage. Tests are authored but not executed in this environment.
