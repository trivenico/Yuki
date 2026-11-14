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

The repository includes the typed contract, durable evidence ledger, owner-scoped
inspection API, and fenced reconciliation workflow described below. It does
not yet include production integrations with any specific provider; those
require provider-specific adapters and separate validation against documented
provider status semantics.

## Durable evidence ledger and owner inspection

Validated adapter evidence can now be appended to the conversation store's
provider-outcome ledger. The write path requires a typed
`ProviderOutcomeEvidence`, an existing unresolved execution, and an exact match
for both the stored idempotency key and argument fingerprint. Each observation
is append-only; it does not update the tool execution's status or authorize a
retry.

The authenticated read-only endpoint is:

`GET /v1/conversations/{conversation_id}/tool-executions/{call_id}/provider-outcomes?request_id={request_id}`

It derives the owner from the authenticated session and returns redacted
evidence metadata. Idempotency keys, argument fingerprints, and opaque provider
references are not exposed by either the read or reconciliation response.
Evidence ingestion remains an internal trusted-adapter
operation; clients cannot submit arbitrary provider claims through the API.

## Explicit adapter registry and reconciliation operation

The runtime now accepts an explicitly constructed
`ProviderOutcomeRegistry`, mapping exact tool names to trusted adapter objects.
There is no dynamic module loading, provider-name guessing, fallback adapter, or
automatic network integration. A `ProviderOutcomeReconciler` loads the
owner-scoped durable execution, passes only its durable identity and status to
the registered adapter, validates the typed evidence, and appends it to the
ledger.

Authenticated reconciliation is requested with:

`POST /v1/conversations/{conversation_id}/tool-executions/{call_id}/provider-outcomes`

Body: `{"request_id":"..." }`

The operation performs one lookup only. Missing adapter configuration returns
a generic service-unavailable response. Adapter transport or parsing failures
do not create a false negative record; the execution remains unresolved and
blocked. The current repository has the adapter contract and registry seam,
not production integrations with any particular email, payment, or cloud
provider. Such integrations must be separately implemented and tested against
each provider's documented status semantics.


## Evidence timestamp validation

Provider evidence timestamps must be timezone-aware UTC. The contract permits
delayed observations, but rejects timestamps more than five minutes ahead of
the Yuki host clock to catch malformed or implausibly future-dated responses.
This is a sanity bound, not proof of provider clock accuracy; adapters should
preserve the provider's original observation time and retain transport-level
timeouts for their own status API calls.


## Duplicate evidence delivery

Evidence persistence runs its duplicate check and insert inside a SQLite
`BEGIN IMMEDIATE` transaction. Re-delivery of the same evidence digest for
the same execution returns the original ledger entry. Reuse of that digest
with conflicting provider, outcome, observation-time, or provider-reference
metadata is rejected. This prevents a digest collision from silently hiding a
different provider-side object reference. This makes evidence ingestion
idempotent across concurrent application workers;
it does not serialize or suppress distinct provider observations.


## Cross-worker reconciliation fencing

A reconciliation request must acquire a durable per-execution lease before
calling the registered adapter. SQLite serializes claim acquisition across
processes; an unexpired claim prevents a second worker from performing the
same provider lookup. Claims carry random fencing tokens and bounded leases.
Expired claims may be replaced, but evidence insertion verifies the current
token and expiry inside the same immediate transaction as the ledger write.
A delayed worker therefore cannot append evidence or release a replacement
worker's claim after its lease expires.

The current claim TTL is bounded to 5–300 seconds (120 seconds by default).
The reconciler renews the claim in a background heartbeat (30 seconds by
default) while the adapter lookup is running. Renewal is conditional on the
same unexpired token; renewal failure is fail-closed and prevents evidence
persistence. Adapters must still enforce their own connect/read/request
timeouts: Python cannot safely terminate arbitrary synchronous adapter code.
Lease fencing protects persistence if an adapter outlives its claim, but does
not cancel the external read-only request.
