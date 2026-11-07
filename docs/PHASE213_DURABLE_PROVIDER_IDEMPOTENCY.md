# Phase 213 — durable provider idempotency

## Delivered

- Optional idempotency key and SHA-256 request fingerprint on durable provider admission.
- Keys are scoped to provider and agent and stored under a unique database constraint.
- Key reservation and execution/lease creation happen in the same SQLite transaction.
- Reusing a key with the same fingerprint raises a typed duplicate exception carrying the original execution ID and current status; it does not create another lease or increment admission counters.
- Reusing a key with a different fingerprint raises a conflict.
- Existing callers without idempotency keys remain compatible.

## Integration boundary

The durable governance store provides atomic key reservation. The governed executor does not yet derive canonical request fingerprints or pass idempotency keys automatically; the application/adapter layer must define stable canonicalization and provider-side idempotency semantics. A local key alone cannot guarantee exactly-once remote execution.

## Safety

No automatic replay or fallback is performed. Idempotency records are retained with execution history; retention/compaction policy must preserve duplicate protection for the full supported retry horizon.
