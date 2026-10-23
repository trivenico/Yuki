# Phase 77 — durable workflow run state

## Implemented

- SQLite-backed workflow run records with unique idempotency keys and request digests.
- Reuse of the existing workflow engine's step validator; duplicate names and malformed steps are rejected before persistence.
- Optimistic version checks and explicit lifecycle transitions.
- Bounded serialized workflow definitions/results and machine-readable error codes.
- Worker leases and attempt counts for running state.
- Expired running leases transition to `interrupted`; the store never silently replays work after an ambiguous crash.
- Unit tests for idempotency, payload mismatch, lifecycle transitions, concurrency versioning, lease expiry, limits, and connection closure.

## Not implemented by this phase

This is a persistence primitive, not a scheduler, queue consumer, webhook server, or n8n-compatible connector runtime. No handlers execute from this module. Lease expiration is reconciled only when the caller invokes the recovery method. Authenticated actor-bound approvals, durable scheduling, retry policy, metrics, and a live multi-process/SQLite contention test remain follow-up work.

## Security notes

Workflow definitions are bounded but may contain user-provided text; protect the database path and backups as application data. Error codes are restricted to identifiers, not free-form exception messages. An expired running job is marked interrupted rather than requeued because external side effects may already have occurred.
