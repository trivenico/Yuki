# Phase 214 — executor-level durable idempotency

## Delivered

- `DurableGovernedProviderExecutor` accepts an explicit reserved `_idempotency_key` argument that is not forwarded to the provider adapter.
- The executor fingerprints the complete normalized request mapping using deterministic JSON serialization and SHA-256.
- Non-JSON or non-finite request values are rejected when idempotency is requested; no unstable repr-based fingerprinting is used.
- The durable store atomically admits the key with its lease/execution record.
- Matching duplicates raise `DurableProviderDuplicate` with the original execution ID and status; no second provider call is made.
- Reusing a key with changed request content is rejected as an admission conflict.
- Calls without an idempotency key remain compatible.

## Important limits

This is Yuki-side duplicate admission protection, not exactly-once remote execution. The key is not yet propagated to concrete provider APIs because provider adapters have no common idempotency contract. A worker crash after the remote provider accepted a request remains uncertain; the same key is not replayed automatically. The caller must inspect/reconcile the original execution.

## Caller contract

Use a stable operation identity scoped to the logical operation, not a fresh random key per retry. The executor fingerprints the actual request mapping, so semantically equivalent but differently normalized requests may conflict; callers should normalize before dispatch.
