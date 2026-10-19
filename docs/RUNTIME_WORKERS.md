# Runtime worker lifecycle — Phase 22

## Implemented

- A bounded worker pool (1–32 workers) consuming the persistent SQLite queue.
- Registered handler mapping only; no payload-selected imports or arbitrary command execution.
- Authorization on submission and immediately before handler execution via `QueuedTaskOrchestrator`.
- Idempotent start and bounded graceful shutdown.
- Per-worker identities for queue claims and basic worker/last-job status.
- A synchronous `run_one` path for deterministic integration tests and single-step operation.

## Operational limitations

- This is an in-process thread pool, not a distributed scheduler. Do not run multiple application instances against a shared SQLite database on network storage without a supported locking/storage review.
- A running Python thread cannot be safely force-killed. Handlers must have explicit I/O deadlines and cancellation cooperation. `close()` returns false when workers remain alive past the shutdown deadline.
- Queue lease expiry/recovery is handled by the queue implementation, but this service does not renew leases for long-running handlers. Keep handler runtime comfortably below the configured lease until lease renewal/fencing is implemented.
- The worker loop retries transient orchestration-level errors after a short delay; durable job retry behavior remains owned by the queue/orchestrator.
- This service is not yet wired into `main.py`; production composition root integration remains a separate phase.
- A successful unit test run does not replace CI, security review, or a clean deployment smoke test.

## Release gate

Run the complete suite on the exact PR head, test concurrent claims and lease expiry under load, test shutdown during real I/O, and add an authenticated submission adapter before exposing this service through an API.
