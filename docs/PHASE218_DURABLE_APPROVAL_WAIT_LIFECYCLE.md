# Phase 218 — Durable approval-wait lifecycle records

## Implemented

- Added additive SQLite table `task_approval_waits`, keyed by task ID and exact claim-token SHA-256 digest. It stores the approval request ID, lifecycle state and timestamps; it never stores the raw lease secret or operation arguments.
- Added task-engine APIs to record, retrieve, list and transition claim-bound wait records. Repeated recording for the same claim/request is idempotent; a different request for the same claim is rejected.
- Worker records the approval request against its live claim before waiting. After credential resolution it requires the corresponding durable wait to be marked delivered before invoking the skill.
- The protected delivery router marks the exact claim's wait as delivered before making the encrypted credential visible. Delivery retries are idempotent at the wait-state layer.
- Approval gateway exposes only redacted lifecycle status. Workers stop waiting promptly on owner rejection or request expiry.
- Lease recovery marks pending/delivered wait records for the expired claim stale in the same SQLite transaction that invalidates the task lease and writes the recovery outbox.
- Task completion closes delivered waits as completed. Task failure/rejection and queued-task cancellation invalidate outstanding waits.
- Added restart/recovery and monotonic transition tests, plus end-to-end assertions that successful execution closes its wait record.

## Security and consistency invariants

- Wait records are scoped to the exact lease generation, not merely task ID or worker name.
- A replacement claim receives a different token digest and cannot reuse the previous claim's approval record.
- Recovery, wait invalidation and task lease invalidation share the task engine's SQLite transaction.
- Request lifecycle status is redacted; no argument JSON, lease token, approval token or credential ciphertext is exposed through the new task-engine projection.
- The worker still revalidates the live lease before dispatch, and the broker still validates the same claim before/after protected delivery.

## Explicit limitations

This phase adds durable approval lifecycle tracking, **not** a separate `awaiting_approval` task state or worker release. The worker process still waits while maintaining its lease. After a process crash, the task lease expires, the old wait becomes stale, and a replacement claim must create a new approval request. A truly independent wait/resume scheduler that releases worker capacity remains future work.

The SQLite task engine is single-host/local-filesystem coordination, not a distributed database. Production multi-process/multi-host deployment needs an explicitly supported shared coordination service or partitioned worker topology. KMS-backed AEAD/key rotation, capability-generated human-readable operation previews, owner approval UI, service-level delivery retries, multi-process crash/fault tests, provider-side commit fencing and release certification remain outstanding.

No local tests were run in this environment. CI on the exact PR head is required before acceptance. This is not a production release.
