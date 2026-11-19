# Phase 93 — Durable sandbox artifact promotion

## Purpose

Make artifact publication recoverable across process crashes without overwriting existing workspace files or assuming that a prior promotion completed.

## Durable protocol

1. Validate the frozen staging tree and persist its canonical manifest.
2. Before approval or publication, persist an immutable promotion intent in `sandbox_promotions`, keyed by `(job_id, artifact_path)`.
3. Bind the intent to the exact destination, source SHA-256, source byte count, actor ID, and approval digest. A later request that changes any bound value is rejected.
4. The promotion service performs its existing explicit approval check and atomic no-overwrite publication.
5. After publication, mark the intent `completed`.
6. Only then update the job's durable promoted-path set and lifecycle state.

## Restart reconciliation

When the same promotion is requested again:

- If the destination is absent and the intent is still `intent`, the service may retry the normal approval and atomic publication path.
- If the destination exists, it is accepted only when its path remains inside the trusted workspace, every parent is a real non-symlink directory, the destination is a single-link regular file, and its size and SHA-256 exactly match the immutable intent.
- An exact existing file allows the lifecycle to complete the durable intent and job-state update without publishing a second copy.
- A mismatched file, symlink, hard link, missing destination for a completed intent, or changed intent fails closed and requires manual reconciliation.
- Existing files are never overwritten or deleted by recovery.

## Boundaries

- Approval remains explicit and bound to the same actor, job, artifact, manifest, and destination.
- The promotion service remains responsible for revalidating source content and performing atomic no-overwrite publication.
- The SQLite intent is recovery metadata, not approval authority.
- No automatic cleanup of mismatched destinations is performed.
- This phase does not wire the sandbox recovery coordinator into application startup and does not enable new agent permissions.

## Verification status

Regression tests were added for intent persistence, completed-state persistence, and reconciliation of an already-published matching artifact. The tests have not been independently executed in this development environment. The PR remains unmerged and deployment is not performed.
