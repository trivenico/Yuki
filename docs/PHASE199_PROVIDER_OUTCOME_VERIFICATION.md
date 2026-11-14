# Provider outcome verification contract

Yuki records tool executions before dispatch and never automatically replays a
started or uncertain external action. Provider outcome verification is a
separate read-only operation.

## Adapter contract

An adapter explicitly implements `lookup_tool_outcome(execution)` and returns
a `ProviderOutcomeEvidence`. The result must bind to the exact durable
`idempotency_key` and `arguments_sha256` already stored for that execution.

Outcomes are deliberately limited to:

- `confirmed_completed`: provider evidence establishes the action completed.
- `confirmed_not_applied`: provider evidence establishes no action was applied.
- `unknown`: evidence is absent, inconclusive, delayed, or contradictory.

A timeout, connection error, missing record, or malformed response must map to
`unknown`, never to `confirmed_not_applied`. Evidence contains a digest rather
than raw provider response data so adapters can retain sensitive payloads in
their own protected storage.

## Safety boundaries

- The contract performs lookup only. It does not retry, resend, or mutate an
  execution record.
- An adapter's presence or idempotency-key support does not establish provider
  retention guarantees.
- A confirmed outcome is not itself permission to retry.
- Any future retry flow must separately validate provider-specific idempotency
  semantics, key retention, exact argument fingerprint, and explicit human
  approval.
- Unknown remains blocked from replay.

The current implementation is a typed contract and validation boundary. It is
not a provider integration, durable evidence ledger, or automatic reconciliation
workflow. Those require provider-specific adapters and a separately audited,
owner-scoped persistence/API layer.
