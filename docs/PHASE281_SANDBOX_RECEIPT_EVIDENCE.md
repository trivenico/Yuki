# Phase 281 — Artifact-bound sandbox execution evidence

## Receipt provenance

Docker sandbox receipts now persist the exact command argument vector as canonical
JSON in addition to command profile ID, immutable image reference, artifact SHA-256,
workspace path, timestamps, exit state, timeout/output-limit flags, and captured
output digest and byte count.

Existing receipt databases are migrated additively with an `argv_json` column.
New writes name their columns explicitly, so the migration's column order does
not affect inserts.

## Evidence validator

`DockerVerificationExecutor.validate_evidence_reference` accepts the reference
form `yuki-verification-execution://<execution_id>` only for the `tests` check.
It requires all of the following:

- receipt exists and has a valid execution ID;
- configured `unit-tests` command exists;
- receipt command ID and exact argv match the current configured profile;
- receipt image exactly matches the digest-pinned executor image;
- receipt artifact digest equals the report's artifact digest;
- status is passed, exit code is zero, and neither timeout nor output truncation
  occurred.

The validator deliberately does not accept a Docker unit-test receipt as a
security-scan result. A separate security profile and independent evidence
source are required for that check.

## Remaining integration work

This phase supplies a receipt validator, but does not yet wire it into the
default evidence store at startup or create a full independent report. That is
left for the next integration step because the evidence store currently has a
single validator callback and the five-check acceptance policy also requires
acceptance criteria, security, architecture, and claims/provenance reviews.
A single unit-test receipt must never satisfy those other gates.

The artifact-to-report workflow still requires a separately authorized reviewer,
and acceptance remains fail-closed until every required check has passed.

## Tests

Extended Docker executor tests to assert persisted exact argv and validate
correct artifact binding while rejecting mismatched artifact digests and using
test evidence for a security check. Tests are authored but not executed here.
