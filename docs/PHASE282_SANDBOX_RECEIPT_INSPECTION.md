# Phase 282 — Artifact-scoped sandbox receipt inspection

## Changes

- Added `DockerVerificationExecutor.list_for_artifact`, a bounded query returning receipt metadata for one exact artifact SHA-256.
- Added an exact-digest filter, deterministic newest-first ordering, and a strict limit from 1 to 100.
- Added `/engineering sandbox-receipts ARTIFACT_ID` to inspect up to 20 receipts associated with the immutable artifact provenance.
- The terminal output includes execution ID, allowlisted command ID, status, exit code, pinned image, and output digest.

## Trust boundary

A successful receipt demonstrates that a configured command completed with exit code zero in the configured Docker invocation. It is not an independent review and does not itself satisfy all required verification checks. The CLI explicitly communicates this distinction.

The existing `VerificationEvidenceStore` still validates test evidence references against successful sandbox receipts. The independent report must separately include all required checks, be authored by a reviewer other than the artifact author, and be linked through the authorized workflow before acceptance can become verified.

## Validation

A unit test covers exact artifact-scoped listing and excludes receipts for another artifact. Tests and Docker execution were not run for this change. The new test uses a mocked process and does not validate real Docker isolation.
