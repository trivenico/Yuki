# Phase 270 — Durable artifact snapshots for verification

## Delivered

`VerificationWorkflow.record_artifact()` now persists the exact artifact
bytes alongside the immutable artifact identity, SHA-256 digest, byte length,
author, task and plan metadata.

`VerificationWorkflow.read_artifact(artifact_id)` retrieves those exact bytes
and rechecks both the stored byte length and SHA-256 digest before returning
them. It fails closed when:
- The artifact does not exist.
- A legacy row has no retained content.
- The stored bytes do not match their recorded length or digest.

Existing databases receive an additive nullable `content` column. Historical
artifact rows are not silently reconstructed: they remain unavailable for
byte-level verification until the original content is re-registered through
the idempotent artifact registration path.

The execution-receipt SQL insert was also corrected to bind the artifact digest
to the explicit `artifact_sha256` column, rather than relying on table column
order. This matters for both fresh and migrated SQLite databases.

## Tests added

`tests/test_verification_workflow.py` now covers exact byte retrieval,
missing legacy content fail-closed behavior, and idempotent registration.

## Important boundary

This establishes a durable, digest-checked artifact snapshot. It does not yet
mean that arbitrary project test suites are automatically executed against
that snapshot. A source-code project usually requires a complete repository
tree, dependency lockfiles, toolchain and a reproducible checkout—not merely
one text output blob. The next phase must introduce a repository snapshot /
workspace materialization contract before claiming end-to-end reproducible
tests.

No tests or CI runs have been executed in this environment.
