# Phase 233 — time-bounded provider verification evidence

Live-pass reports now require an exact model identity, timezone-aware
`verified_at` and `expires_at`, and a strictly increasing validity interval.
The readiness evaluator rejects missing, malformed, future-dated, expired,
or model-mismatched evidence. Expiry is exclusive: evidence is invalid at
`now >= expires_at`.

The evaluator accepts an injectable timezone-aware clock for deterministic
boundary tests. Coordinator planning and dispatch both call this evaluator
through the trusted readiness resolver, so stale evidence fails closed at
both boundaries.

No live provider check was run in this phase. A real conformance process must
supply the report fields; a status string alone is insufficient.
