# Phase 281 — Bind sandbox execution to immutable artifact identity

## Change

The Docker verification executor now requires both:

- `expected_snapshot_sha256`: digest of the materialized source tree.
- `expected_artifact_sha256`: digest of the immutable artifact bundle recorded by the Git-backed workspace manager.

At execution time, the caller-supplied artifact digest must equal the configured artifact digest before Docker is launched. This closes a binding gap where a valid workspace could previously be executed while the caller supplied an unrelated artifact digest for the receipt.

The normal terminal path supplies both digests from the manager's persisted provenance record. The receipt continues to retain the exact allowlisted argv, immutable image reference, artifact digest, output digest, exit status, and bounded output excerpt.

## Tests added

- Reject a mismatched artifact digest before process launch.
- Keep the existing successful receipt/evidence-reference test.
- Keep workspace tampering and symlink-root rejection coverage.
- Update executor construction to require the artifact digest.

## Operational limits

This is a provenance-binding improvement, not a claim of a complete hostile-code sandbox. Docker daemon access remains a powerful host capability; container isolation depends on the host kernel, Docker configuration, and daemon trust. No Docker execution or test suite was run as part of this source change. The unit tests mock Docker process launch and do not prove real container isolation.

## Follow-up

The next integration step is to create independent verification reports from successful sandbox receipts, link those reports to immutable artifacts, and gate engineering acceptance on the resulting reviewer decision. A passing command receipt alone must not be treated as independent review or final acceptance.
