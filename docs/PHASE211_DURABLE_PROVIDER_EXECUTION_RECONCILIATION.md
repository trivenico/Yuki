# Phase 211 — durable provider execution correlation and reconciliation

## Delivered

- Every durable admission creates a unique execution ID and a durable execution record in the same SQLite transaction as its lease.
- Provider outcomes update the correlated execution record.
- Lease expiry transitions an unfinished execution to `uncertain`, rather than implying success or failure.
- `list_executions` exposes bounded, provider-scoped operational records and defaults to unresolved `running` and `uncertain` executions.
- Records contain identifiers, status, timestamps and bounded reason codes only; prompts, response bodies, credentials and provider payloads are not persisted.
- No automatic replay, retry, or provider fallback is introduced.

## Recovery semantics

A lease expiry means the worker stopped renewing/finishing its reservation; it does **not** prove the remote provider did not complete the request. Such executions are marked `uncertain` for operator or higher-level workflow reconciliation. This phase does not resolve uncertain outcomes automatically and does not claim provider-side idempotency.

## Deployment boundary

The records coordinate processes sharing a SQLite database on a filesystem with reliable SQLite locking. Multi-host deployments still require a shared network database with equivalent transactional guarantees. Provider execution remains opt-in through the durable governed executor.
