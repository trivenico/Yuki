# Phase 199: Uncertain Tool Outcome Review Audit

Yuki records a tool execution as `uncertain` when a handler fails after dispatch or a worker exits before a durable completion record can be written. This status means Yuki cannot prove whether the external side effect committed.

## Owner-scoped review

Authenticated conversation owners can append an `acknowledge_uncertain` review event for an execution that is currently uncertain:

- `POST /v1/conversations/{conversation_id}/tool-executions/{call_id}/reviews`
- JSON body: `{"request_id":"...","action":"acknowledge_uncertain","reason":"..."}`
- `GET /v1/conversations/{conversation_id}/tool-executions/{call_id}/reviews?request_id=...`

The server derives the actor from the authenticated session. The request cannot supply or override an owner ID. Reviews are append-only records containing the execution identity, action, bounded operator reason, and timestamp. Access is constrained by the conversation owner and execution owner.

A review is an acknowledgement/audit event only. It does not change the execution's `uncertain` status, establish whether a provider committed the operation, or authorize another dispatch. Acknowledgement can be recorded more than once to preserve a sequence of operational reviews.

## Explicit safety boundary

This phase intentionally does not implement provider status lookup or retries. A human-written reason is not authoritative provider evidence. Yuki must not convert an execution to `confirmed_completed` or `confirmed_not_applied` based on a note, nor retry because an operator acknowledged it.

A future reconciliation adapter must separately define provider-side lookup semantics, authenticated evidence, provider idempotency guarantees and retention windows. Any consequential retry must require explicit approval and reuse the exact persisted idempotency key and argument fingerprint. Unknown outcomes remain blocked. Exactly-once external execution is not claimed.
