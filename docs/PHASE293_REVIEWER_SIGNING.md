# Phase 293 — Reviewer signing primitives

## Implemented

Adds canonical JSON serialization and HMAC-SHA256 signing/verification for
review-result payloads. Signatures carry a key identifier and explicit
algorithm identifier. Verification uses constant-time digest comparison.

## Key management boundary

The signer accepts key bytes from its caller and never writes them to SQLite or
logs. The host must provision keys from protected storage, restrict access,
rotate keys, and retain old verification keys for the required audit period.
A local HMAC key protects against database-only modification only when the key
is outside the database and inaccessible to the attacker.

## Integration status

This phase adds the cryptographic primitive and tests. The existing SQLite
review ledger and verification bridge are not yet configured to require or
persist signatures. Therefore existing review submissions must not be described
as cryptographically signed or signature-verified. A subsequent integration
phase must persist signature envelopes and make the verification bridge reject
unsigned or invalid results before they enter VerificationEvidenceStore.

## Tests

Four focused tests cover exact-payload verification, tampering, key-ID mismatch,
and minimum key length. Tests are authored but not executed in this environment.
