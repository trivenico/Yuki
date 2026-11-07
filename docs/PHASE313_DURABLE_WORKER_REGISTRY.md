# Phase 313 — Durable reviewer worker registry

## Scope

Adds a SQLite-backed registry for worker process instances and globally shared
per-specialist concurrency slots. The registry is independent of task
execution and stores no session tokens, lease credentials, task payloads, or
signing keys.

## Operations

- Register a worker instance with an opaque bearer lease token (only its SHA-256
  digest is stored).
- Renew worker liveness with a heartbeat; expired leases cannot be revived.
- Atomically reserve a specialist-wide concurrency slot under
  `BEGIN IMMEDIATE`, across processes sharing the same SQLite database.
- Release a slot using the owning instance's lease.
- Mark a worker stopped and release its slots.
- Expire stale instances and inspect worker state and active slot counts.

## Crash recovery boundary

A worker whose heartbeat expires is reported as `expired` and can be
identified as a recovery candidate. This registry does not itself reassign
review tasks or change reviewer task leases. The existing reviewer lease
authority and recovery orchestrator remain responsible for task-level fencing
and reassignment. This prevents worker-liveness expiry from being confused
with proof that an in-flight review is safe to replay.

## Deployment constraints

All processes enforcing a given specialist's concurrency cap must share the
same SQLite database file on a filesystem that correctly supports SQLite
locking. This is not a distributed database guarantee for arbitrary network
filesystems. For multi-host deployments, use a transactional shared database
or dedicated coordination service before claiming global concurrency.

## Security

All operations are authorization gated. Instance tokens are random bearer
secrets; only SHA-256 digests are persisted. Tokens are returned only at
registration and must remain in trusted host storage. The worker registry must
not be exposed directly to reviewer model/container code.

## Verification

Run:

```bash
python -m unittest tests.test_reviewer_worker_registry -v
```

Tests cover registration, heartbeat, shutdown, cross-instance concurrency
enforcement, token validation, and fail-closed authorization. They have not
been executed as part of this change unless CI reports otherwise.
