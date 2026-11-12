# Phase 215 — Restart-safe supervised lease recovery

## Implemented

- Lease expiry now writes a credential-revocation outbox row in the same SQLite transaction that clears the task's worker, expiry and lease token.
- The outbox stores only the SHA-256 claim-token digest, task ID, worker ID and processing metadata; it never stores the raw lease secret.
- Added bounded, leased outbox claiming with supervisor identity, retry attempts, lease expiry, explicit release and per-claim random fencing tokens for acknowledgement. A stale supervisor process cannot acknowledge a later lease, even if the same supervisor identity is reused.
- Added broker revocation by task/worker/claim digest. Revocation is idempotent and erases ciphertext for unused credentials.
- Added `LeaseRecoveryCoordinator`, which routes each outbox item only to the broker pinned to the recorded worker identity and acknowledges only after successful broker cleanup.
- Missing brokers and broker errors leave work retryable. A supervisor crash after revocation but before acknowledgement is safe because the next pass repeats an idempotent revocation.
- Added tests for transactional outbox creation, supervisor claim exclusivity, release/reclaim, acknowledgement, and recovery after reopening the task engine.

## Safety properties

- The task-engine transaction invalidates the old lease before any asynchronous revocation work begins. Broker resolution independently validates the current task lease, so the old credential is unusable as soon as recovery commits, even if the coordinator is offline.
- Outbox records are durable and contain no raw worker credential.
- Acknowledgement is conditional on the current supervisor claim.
- Credential cleanup is at-least-once, not exactly-once; the broker operation is intentionally idempotent.
- The coordinator never falls back to a different worker's broker when a worker-specific broker is missing.

## Remaining production requirements

- The supervisor still needs a production service lifecycle, authenticated configuration and health/alerting around deferred outbox age and repeated failures.
- A broker database outage delays physical ciphertext erasure, although the cleared task lease prevents resolution of the old credential.
- This phase does not terminate stale worker processes or undo remote operations already in flight.
- Provider adapters need generation-aware commit checks, idempotency keys, transactional outbox/reconciliation and compensation where supported.
- Multi-process fault injection, crash-at-each-boundary tests, load tests, production KMS integration, deployment backup/restore exercises and release certification remain outstanding.

This phase is an integrated recovery primitive, not a production release. Do not claim complete recovery safety until supervised deployment, provider-side commit fencing and end-to-end operational tests pass.
