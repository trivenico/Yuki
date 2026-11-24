# WI-6U13H — External side-effect idempotency and recovery

**Status:** design and implementation-boundary audit  
**Decision:** no external-action execution is enabled by the current specialist worker. Do not treat this document as a provider integration or production approval.

## 1. Current implementation finding

The current `SpecialistTaskWorker` accepts registered callables, validates a single `emit` step, invokes the handler, and persists the returned text as a task result. The built-in provider-backed specialist handlers call text-generation adapters and return bounded text. Their documented contract is no side effects: they do not execute generated code, run shell commands, modify the workspace, or implicitly perform network actions.

The durable PostgreSQL workflow repository already provides idempotent task creation by `idempotency_key` plus request-digest conflict detection; atomic task state and event writes; lease owner/generation/expiry fencing; and recovery of expired running tasks to `interrupted`, with `automatic_replay=false`.

These controls protect workflow admission and task state. They do **not** make an arbitrary external provider operation exactly-once. A process can lose its connection after a provider accepted an operation but before Yuki recorded the result. Repeating that operation blindly can duplicate the effect.

## 2. Required execution boundary before adding side-effecting tools

Do not allow a specialist handler to call a payment, messaging, deployment, ticketing, or other mutating provider directly. Side-effecting capabilities must be represented as explicit, typed action adapters with a fixed action name/version; schema-validated bounded input; tenant, task, step, and delegated-scope binding; authorization and approval for the exact action intent; an adapter-owned provider idempotency capability or query/reconciliation capability; bounded deadlines and response sizes; and redacted structured outcomes.

Model output may propose an action but must never choose its own trust identity, tenant, delegation, idempotency namespace, provider credential, or approval state.

## 3. Stable operation identity

For each externally visible step, derive one stable operation key from immutable, canonical fields:

```text
operation_key = SHA-256(
  canonical_json({
    tenant_id,
    task_id,
    step_id,
    action_name,
    action_version,
    input_digest
  })
)
```

The canonical input digest is computed after schema validation and normalization. It excludes transient lease owner, lease generation, retry count, timestamps, and provider credentials. Those values change across retries and must not create a new logical operation.

Enforce a unique database constraint on the operation key. Reuse with the same action/version/input digest returns the existing operation. Reuse with a different digest or action identity is a hard conflict and must be audited.

## 4. Durable operation journal and outbox

Before any provider call, commit an operation intent to PostgreSQL. A future migration (proposed `0009_external_operations.sql`, not created by this audit) should define an append-only event journal and current operation projection with at least:

- operation key (unique), tenant/task/step, action name/version, canonical input digest;
- state: `pending`, `inflight`, `succeeded`, `failed_terminal`, `unknown`, or `reconciliation_required`;
- provider name and provider-side idempotency key (never credentials);
- attempt count, bounded next-attempt time, lease owner/generation/expiry;
- provider operation/reference ID, normalized result digest, timestamps;
- created/updated timestamps and monotonic version.

The operation intent must be committed before dispatch. Claiming must use `FOR UPDATE SKIP LOCKED` and database-clock leases. Every state mutation must be fenced by owner, generation, unexpired lease, and expected version in the same transaction as its event append. Stale workers must be unable to mark an operation successful or failed.

Task and operation state cannot be assumed to commit atomically across a provider boundary. The operation journal is authoritative for the external effect; task completion is a projection that can be reconciled from the durable operation result.

## 5. Provider dispatch, retry, and uncertain outcomes

Dispatch must send the same stable provider idempotency key for every attempt of the same logical operation, if and only if the provider documents and guarantees that behavior for the relevant API and retention period.

Classify outcomes:
- **Definitive success:** persist provider reference and normalized result, then mark operation succeeded.
- **Definitive terminal rejection:** persist a redacted terminal outcome; do not retry unchanged input.
- **Definitive transient rejection before acceptance:** retry with bounded exponential backoff and jitter, preserving the same operation key.
- **Timeout, connection reset, worker crash, or ambiguous response after dispatch:** mark or leave the operation `unknown`; reconcile with the provider by its idempotency key/reference before another dispatch.
- **Provider lacks idempotency and lookup/reconciliation:** do not automatically retry an ambiguous mutating request. Require operator reconciliation or a provider-specific safe policy.

A database lease expiry alone is not evidence that the provider operation did not happen. Recovery may transfer ownership of reconciliation, but must not turn an unknown operation into a fresh dispatch.

## 6. Crash matrix that must be tested

| Crash point | Required recovery |
|---|---|
| Before intent commit | No provider call; safe to create intent |
| After intent commit, before dispatch | Claim pending intent and dispatch once |
| During dispatch, provider definitely did not accept | Retry under the same operation key |
| Provider accepted, response lost | Query/reconcile or replay the same provider idempotency key; never mint a new key |
| Provider accepted, process crashed before result commit | Reconcile provider outcome, then persist result |
| Result committed, task projection not updated | Rebuild task projection from operation journal without calling provider |
| Old worker resumes after lease transfer | Database fencing rejects all stale operation writes |

## 7. Acceptance tests required before enabling a mutating adapter

1. Concurrent workers claim one operation; only one current lease owner can commit.
2. Same operation key and same input return the same durable operation.
3. Same operation key with a different input/action is rejected and audited.
4. Provider receives the identical idempotency key across retries and process restarts.
5. Simulated provider acceptance followed by connection loss reconciles to one provider operation.
6. Crash after provider success but before DB result commit does not create a second effect.
7. Lease expiry and stale worker resumption cannot overwrite newer operation state.
8. A provider without idempotency or lookup support does not receive automatic retry after an ambiguous result.
9. Task result projection can be rebuilt without re-dispatch.
10. Secrets and raw provider payloads are absent from operation events, logs, and error codes.

Tests must use a deterministic fake provider that records accepted idempotency keys and can inject failures at each crash point, followed by PostgreSQL integration tests for the operation journal. Passing unit tests alone does not establish a provider's real idempotency guarantees.

## 8. Rollout gates

- Keep current text-only specialist handlers as the only enabled handlers until a concrete mutating adapter is reviewed.
- Implement the PostgreSQL operation journal and transactional fencing before adding any mutating provider action.
- Verify the selected provider's idempotency-key semantics, retention window, lookup API, and failure behavior from authoritative provider documentation.
- Require exact-head CI, PostgreSQL concurrency/crash tests, authorization/delegation tests, and operator reconciliation runbooks.
- Enable one narrowly scoped action at a time, behind an explicit deployment feature flag and per-tenant authorization policy.
- Do not claim exactly-once delivery. The achievable contract is effectively-once behavior only where provider idempotency/reconciliation semantics support it; otherwise ambiguous outcomes require reconciliation.

## 9. Explicit non-goals

This audit does not add a generic provider dispatcher, does not create migration 0009, does not enable payments/messages/deployments, does not introduce automatic replay of interrupted tasks, and does not certify external side effects as exactly once. The existing specialist provider handlers remain text-generation-only.
