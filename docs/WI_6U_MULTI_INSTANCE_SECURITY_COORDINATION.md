# WI-6U — Multi-instance security coordination architecture

## Status

Architecture proposal only. This document does not claim multi-instance security coordination is implemented or production-ready.

WI-6U is deliberately downstream of the WI-6T security-pipeline acceptance chain. Do not begin a multi-host rollout until the dependent security PRs have passed their required CI checks and the single-host recovery gate has been accepted.

## Decision

Use PostgreSQL as the shared transactional authority for multi-instance security state and worker coordination. Keep the existing SQLite implementations as the supported single-host/local mode.

Do not put SQLite databases on a network filesystem and do not treat SQLite WAL as a multi-host coordination mechanism. Do not attempt to make separate SQLite files behave like one distributed transaction.

Yuki already has a PostgreSQL conversation-store implementation and configuration/pooling conventions. Reuse those operational patterns (verified TLS, bounded pools, startup/close lifecycle, dependency injection), but do not reuse the conversation store as a security repository. Security state has different invariants and needs its own schema, transaction boundaries, migrations, and tests.

## Current baseline and gap

The security pipeline currently consists of separate SQLite-oriented components:

- ingestion cursors and fenced source leases;
- alert outbox claims, retry state, and dead-letter state;
- workload signing-key lifecycle and audit events;
- normalized security monitor signals and incidents;
- worker heartbeats and readiness aggregation;
- local authorization audit history and independent external checkpoint integration.

Those stores are individually durable, but separate SQLite databases do not provide a single atomic transaction across ingestion, monitor, outbox, key lifecycle, and audit state. The current in-process supervisor owns worker threads in one process. Lease fencing protects stale workers within the current store's consistency boundary; it does not itself create a cross-host shared authority.

The existing PostgreSQL conversation store is a useful connection-pool and configuration precedent, not evidence that these security stores already support PostgreSQL.

## Target topology

- One PostgreSQL primary (or managed HA service with a single writable primary) is authoritative for shared security state.
- Any number of Yuki application/worker replicas may connect through TLS and bounded connection pools.
- Each worker has a stable deployment identity and a unique process/instance identifier. A worker role is logical; several instances may compete for work for that role.
- The external audit checkpoint service remains an independent trust boundary. It must not be moved into the same PostgreSQL trust/failure domain as the local audit chain.
- Provider clients, signing secrets, KMS/HSM access, and identity bootstrap remain host/deployment responsibilities.
- SQLite remains available for local-first single-host deployments. A process must select one backend at startup; no implicit mixed-backend mode.

## Repository and schema boundaries

Introduce a security persistence contract and explicit backend implementations rather than branching SQL throughout business logic. The initial repository boundaries should be:

1. `SecurityIngestionRepository`: source cursor, claim, renew, complete, fail, and fencing metadata.
2. `SecurityAlertOutboxRepository`: idempotent incident ingestion, claim, renew, success, retry, and dead-letter transitions.
3. `WorkloadKeyRepository`: key metadata, activation/retirement/revocation state, and append-only key audit events.
4. `SecurityMonitorRepository`: normalized signals, incident state, and incident history.
5. `SecurityWorkerLivenessRepository`: worker instance heartbeat, role, last successful tick, and sanitized failure state.
6. `SecurityAuditRepository`: append-only local audit records and checkpoint metadata, with the independent anchor client remaining a separate adapter.

The first implementation may share one PostgreSQL database and transaction manager, but each repository must own its table constraints and public invariants. Use versioned schema migrations; do not rely on constructor-time ad hoc schema mutation as the long-term migration mechanism.

Keep credentials and private signing material out of these tables. Store only public key metadata/fingerprints and revocation state. Do not put alert payload secrets or provider credentials in worker heartbeat/error columns.

## Lease and claim protocol

All cross-instance claims must be serialized by PostgreSQL transactions:

1. Start a short transaction.
2. Select eligible work using row-level locking (normally `FOR UPDATE SKIP LOCKED`) or an equivalent atomic compare-and-set.
3. Assign a fresh unguessable lease token, owner instance ID, expiry, and monotonically increasing fencing epoch/version.
4. Commit before performing network/provider work.
5. Renew only when owner, token, epoch/version, and unexpired lease all match.
6. Complete/fail only when the same ownership tuple still matches and the lease remains unexpired.
7. A stale worker must receive a conflict and must not mutate replacement-worker state.

Use database time for lease expiry comparisons so workers with skewed host clocks cannot disagree about ownership. Keep lease duration bounded. Network calls must have deadlines shorter than the remaining lease; renew before the safety margin. Never hold a database transaction or row lock across a provider/network request.

A random token prevents accidental reuse; the monotonically increasing fencing epoch protects downstream systems that can enforce fencing. Where an external provider cannot enforce a fencing epoch, delivery remains at-least-once and must use a stable idempotency key. Do not claim exactly-once external side effects.

## Atomicity and idempotency

Within PostgreSQL, use one transaction when a state transition truly spans repositories—for example, recording an ingested signal and advancing its source cursor, or transitioning an incident and enqueueing its alert notification. Preserve deterministic source event IDs and unique constraints as the final duplicate barrier.

For external delivery, commit the outbox claim before the network call, send a stable alert ID/idempotency key, then acknowledge only with the current lease fence. If a process crashes after the provider accepts a request but before acknowledgement, retry may occur; provider-side deduplication is required to suppress duplicate effects.

The external audit checkpoint cannot participate in the PostgreSQL transaction. Preserve the current fail-closed reconciliation model: local history may not be silently rewritten behind the independently anchored checkpoint. Define explicit pending-checkpoint and retry behavior; never advance local trust based solely on an unverified local database row.

## Worker ownership and liveness

Replace the assumption of one in-process worker per role with distributed claims. A supervisor remains responsible for local thread/process lifecycle, but it is not the distributed lock manager.

- Each instance publishes liveness with a heartbeat interval and bounded stale threshold.
- Readiness must distinguish process liveness, role availability, source-cursor freshness, outbox backlog, dead letters, and audit-anchor status.
- A heartbeat from one replica must not make a failed role on every other replica appear healthy.
- A role may have multiple healthy instances; report aggregate and per-instance health without exposing secrets or raw exception text.
- Graceful shutdown stops claiming new work, then drains bounded in-flight work. Lease expiry/fencing remains the crash-recovery mechanism.
- A blocked operation must have a transport timeout; Python thread shutdown alone cannot safely interrupt an unbounded call.

## Failure model and explicit non-goals

WI-6U initially targets multiple replicas in one deployment region using one writable PostgreSQL primary. It does not initially promise:

- active-active multi-region writes;
- transparent operation through database failover without retry/reconciliation;
- exactly-once provider side effects;
- automatic replay of arbitrary interrupted agent/tool executions;
- distributed transactions with the independent audit-anchor service;
- a distributed consensus system built inside Yuki.

Database failover may interrupt in-flight transactions. Clients must use bounded connection/query timeouts, retry only operations with safe idempotency semantics, and surface temporary readiness failure. Never retry a non-idempotent provider action blindly.

## Migration and rollout

1. Complete and accept WI-6T's single-host security release gate.
2. Add versioned PostgreSQL schema migrations and repository interfaces without changing SQLite behavior.
3. Add PostgreSQL implementations and contract tests that run against a real PostgreSQL service in CI.
4. Test concurrent claim races, lease expiry, stale completion/failure, worker restart, process kill, DB connection loss, and primary failover.
5. Provide an explicit offline export/import tool for each SQLite store. Validate counts, uniqueness, cursor monotonicity, audit-chain integrity, key revocation state, and independent checkpoint agreement before cutover.
6. Do not dual-write security state to SQLite and PostgreSQL. Use a maintenance window or controlled single-writer cutover with a verified backup and rollback plan.
7. Start with one worker replica, observe liveness and queue progress, then increase replica count.
8. Keep rollback forward-only for security state: never restore revoked keys or roll local audit history behind an external checkpoint.

## Required acceptance tests

- Two or more independent PostgreSQL connections race to claim one item; exactly one current lease wins.
- A stale lease holder cannot renew, complete, fail, or dead-letter work after expiry/replacement.
- Database time governs expiry even when worker clocks differ.
- Transaction rollback leaves cursor and corresponding signal/outbox transition consistent.
- Replayed source pages do not duplicate monitor signals or alert outbox records.
- Crash after provider acceptance but before outbox acknowledgement produces a safe retry with the same idempotency key.
- Revoked keys remain revoked across process restart and database failover/restore.
- Independent audit checkpoint detects local truncation, rewritten history, and restore to an older snapshot.
- Heartbeat/readiness identifies a failed instance without allowing another instance's heartbeat to mask it.
- Shutdown stops new claims and bounded in-flight work; abandoned leases recover after expiry.
- Connection pool exhaustion, PostgreSQL unavailability, TLS verification failure, and failover produce bounded, sanitized failure behavior.
- Restore drills validate schema version, constraints, audit state, cursor state, outbox state, and key revocation state.

## CI and operational gates

- Run repository contract tests against SQLite and PostgreSQL implementations.
- Run concurrency tests against the supported PostgreSQL major version, not SQLite emulation alone.
- Use least-privilege database roles; separate migration privileges from runtime privileges.
- Require TLS certificate and hostname verification for remote PostgreSQL.
- Bound pool size across all replicas against the database connection budget.
- Monitor pool saturation, transaction duration, lock waits, deadlocks, lease churn, stale workers, cursor lag, outbox age/dead letters, and checkpoint freshness.
- Back up PostgreSQL off-host, encrypt backups, test point-in-time restore where supported, and perform restore drills.
- Keep schema migrations backward-compatible across rolling deployments; use expand/contract migrations and do not drop old columns while old replicas may still run.

## Exit criteria

WI-6U is not complete until PostgreSQL-backed security repositories, versioned migrations, real PostgreSQL concurrency/failure tests, explicit SQLite migration tooling, and operational restore/failover evidence exist. A design document or a green SQLite-only suite is not sufficient evidence for multi-instance readiness.
