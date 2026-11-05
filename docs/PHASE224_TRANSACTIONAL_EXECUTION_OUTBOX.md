# Phase 224 — transactional execution event outbox

- Added an outbox row in the same SQLite transaction as every execution event, including execution.created.
- Added bounded leasing, retry scheduling, acknowledgement, and pending/leased/delivered counters.
- A lease can be acknowledged or rescheduled only by its current worker owner.
- Expired leases become claimable again; retries do not rerun agent tasks or provider operations.
- Outbox payloads are joined from the existing privacy-safe append-only event journal; arbitrary payload fields are not introduced.

Operational boundary: this phase provides a durable local SQLite outbox and claim/ack API, not a network sink dispatcher, multi-host queue, or exactly-once delivery. Consumers must use event_id as their idempotency key because delivery is at-least-once. A future dispatcher should send only allowlisted event fields, acknowledge after confirmed sink acceptance, and apply bounded exponential backoff with dead-letter/operator review policy.
