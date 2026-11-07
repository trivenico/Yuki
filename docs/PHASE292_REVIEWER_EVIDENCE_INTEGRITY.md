# Phase 292 — Reviewer evidence integrity

## Implemented

- Ledger reads recompute canonical task and result SHA-256 digests.
- Persisted task JSON is reconstructed and validated against denormalized row identity.
- Submitted result JSON is reconstructed into typed findings and validated against its task.
- Lifecycle state, version, event count, sequence, event actor, and event payload are checked together.
- The verification bridge now requires an explicit trusted evidence-reference validator.
- Task evidence references and every finding evidence reference are checked against the exact artifact digest before a verification report is created.

## Trust boundaries

The event history is consistency-checked, not cryptographically tamper-proof against an attacker who can rewrite the SQLite database and recompute all unkeyed hashes. Stronger tamper evidence requires an externally protected signing key, append-only remote log, or independently anchored checkpoint. Evidence validation is delegated to a trusted callback because the bridge cannot infer whether arbitrary references resolve to genuine artifact bytes.

## Compatibility

Callers constructing ReviewerVerificationBridge must now provide
`evidence_reference_validator(reference, artifact_sha256) -> bool`.
The callback must return literal `True`; errors and false values fail closed.

## Tests

Added tampered task payload and event history tests, plus bridge evidence-validator rejection coverage. Tests are authored but not executed in this environment.
