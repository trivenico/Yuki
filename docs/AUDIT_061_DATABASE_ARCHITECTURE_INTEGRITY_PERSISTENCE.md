# Yuki Engineering Audit 061 — Database Architecture, Data Integrity, and Persistence Reliability

**Review date:** 2026-11-23  
**Repository:** `trivenico/Yuki`  
**Review type:** Targeted source and test inspection  
**Status:** Architecture and code review; not a production reliability certification.

> Scope note: This audit inspected the conversation persistence implementation, its factory, SQLite file/recovery utilities, configuration tests, and related test files on `main`. It did not connect to a production database, execute the test suite, inspect every persistence subsystem, or verify backup/restore operations.

## Executive assessment

Yuki has a meaningful persistence foundation:

- SQLite conversations use owner-scoped queries, foreign keys, WAL mode, optimistic versions, idempotency keys, generation leases, and bounded pagination.
- PostgreSQL conversation persistence uses a bounded psycopg connection pool and shares much of the conversation-store contract.
- SQLite files are prepared with restrictive POSIX permissions and reject symlinks, non-regular files, and hard links.
- SQLite startup diagnostics perform read-only integrity checks and deliberately avoid overwriting damaged stores.
- Tests cover owner scoping, optimistic conflicts, idempotent replay, recovery behavior, and optional PostgreSQL integration.

The main production-readiness gaps are schema migration discipline, verified live PostgreSQL parity, operational backup/restore, tenant identity propagation, storage observability, and failure testing across process crashes and real concurrency. The current inspected conversation schema is owner-scoped; this is not equivalent to a fully implemented organization/tenant model.

## 1. Inspected components and findings

### `core/conversation_store.py`

Observed:
- Creates SQLite tables for conversations, messages, generation leases, and generation runs.
- Enables WAL and foreign keys.
- Uses owner-scoped queries for conversation reads and writes.
- Uses optimistic version checks and unique idempotency keys.
- Uses bounded input sizes, message counts, and pagination.
- Uses explicit transactions for multi-step operations and generation lifecycle changes.

Recommendations:
- Keep all state transitions transactional and add crash-point tests around each multi-step operation.
- Verify every method scopes reads and writes to the authenticated owner; callers must never accept arbitrary owner IDs from untrusted input.
- Add database-level tenant scope if organization tenancy is introduced.
- Add indexes based on measured query plans and realistic data volume.
- Document SQLite's single-writer characteristics and define when a deployment must move to PostgreSQL.

### `core/postgres_conversation_store.py`

Observed:
- Uses psycopg and `ConnectionPool`, with validated pool bounds and connection wait timeout.
- Uses PostgreSQL-specific schema creation and a small SQL compatibility adapter for shared store methods.
- Exposes an explicit `close()` method.
- The pool is configured with autocommit and shared multi-statement operations explicitly issue transaction boundaries through the shared store logic.

Recommendations:
- Run the same behavioral contract suite against a real PostgreSQL service in CI, not only SQL translation tests.
- Test rollback and pool connection reset after exceptions, deadlocks, cancellation, and statement timeout.
- Define application shutdown ownership so the pool is always closed.
- Configure server-side statement, idle-transaction, and connection timeouts.
- Add pool saturation, connection churn, and database failover tests.
- Avoid leaking the database URL through reprs, logs, diagnostics, or exception telemetry.

### `core/conversation_store_factory.py`

Observed:
- SQLite is the default backend.
- PostgreSQL is selected explicitly and configured with bounded pool sizes.
- PostgreSQL dependencies are optional in `pyproject.toml`.

Recommendations:
- Validate production backend selection at startup and fail clearly when the selected optional dependency is unavailable.
- Make storage backend selection explicit in production deployment configuration; do not silently fall back from PostgreSQL to local SQLite.
- Validate that pool limits fit the total process/replica count and the database connection budget.

### `core/sqlite_file_security.py`

Observed:
- Creates private directories and SQLite files.
- On POSIX, applies owner-only file permissions.
- Rejects symlinks where supported, non-regular files, and files with multiple hard links.
- Explicitly documents that permissions are not encryption and Windows ACLs need separate deployment handling.

Recommendations:
- Verify directory permissions and ownership across every path component and supported operating system.
- Treat same-OS-user access as inside the trust boundary; use OS account isolation and encryption where required.
- Document backup files, WAL/SHM sidecars, temporary files, and copied database artifacts in the data-protection model.
- Add platform-specific tests for Windows ACLs and POSIX permission behavior.

### `core/sqlite_recovery.py`

Observed:
- Performs read-only SQLite `quick_check` diagnostics.
- Reports missing, unsafe, empty, corrupt, and unavailable stores separately.
- Does not disclose database paths or contents in the owner-facing recovery report.
- Blocks startup when an existing store needs attention rather than initializing over it.

Recommendations:
- Add a documented recovery runbook with verified backup selection, restore procedure, integrity verification, and rollback.
- Test recovery from corrupt database, missing WAL, disk-full, permission failure, and interrupted atomic writes.
- Add periodic integrity checks for critical stores, with alerting and bounded resource usage.
- Ensure diagnostics cover all production stores, not only SQLite paths.

### `core/json_file_store.py`

Observed:
- Uses thread and process locks for read-modify-write operations.
- Writes JSON through a temporary file, fsync, atomic replace, and parent-directory fsync on POSIX.
- Documents that this is not encryption and assumes a private parent directory.

Recommendations:
- Keep JSON stores for bounded local state only; avoid using them as a distributed multi-worker database.
- Verify lock semantics on each supported OS/filesystem, especially network filesystems.
- Add disk-full, interrupted-write, stale-lock, and multi-process contention tests.
- Ensure every JSON store follows the same locking contract; do not implement ad-hoc read/modify/write code elsewhere.

## 2. Schema migration and versioning — P0

The inspected conversation stores create tables and indexes with `CREATE TABLE IF NOT EXISTS`. No explicit migration ledger or ordered migration framework was found in the inspected files.

Required:
1. Introduce a schema migration mechanism with ordered, immutable migration IDs.
2. Track schema version in a dedicated migration table.
3. Apply migrations transactionally where the database supports it.
4. Use expand/migrate/contract changes for rolling deployments.
5. Verify the application version is compatible with the current database schema before serving traffic.
6. Back up before destructive migrations and define rollback or forward-fix behavior.
7. Test upgrades from every supported released schema, including interrupted migration recovery.
8. Prevent two application replicas from applying the same migration concurrently using a database advisory lock or equivalent.

Suggested layout:

```text
migrations/
  sqlite/
    0001_initial_conversations.sql
    0002_generation_leases.sql
  postgres/
    0001_initial_conversations.sql
    0002_generation_leases.sql
```

A shared logical migration set may be preferable if SQL differences can be managed safely. Do not assume SQLite and PostgreSQL DDL/transaction semantics are identical.

## 3. Transaction and concurrency invariants

- Every logical multi-row update is one transaction.
- Every write that depends on a prior read uses optimistic version checks or appropriate row locks.
- Idempotency keys are unique within their intended owner/resource scope.
- A worker whose lease has expired or been superseded cannot commit stale output.
- Retried transactions distinguish serialization/deadlock errors from permanent failures.
- External side effects are not assumed to be rolled back by a database transaction.
- Connection pool checkout and transaction lifetimes are bounded.
- Background jobs and workers use the same ownership and tenant predicates as request handlers.

Required failure tests:
- Two workers append messages at the same version.
- Two replicas acquire the same generation lease.
- Worker loses lease while model execution continues.
- Process exits after insert but before response.
- Process exits during a multi-row state transition.
- PostgreSQL deadlock or serialization failure triggers bounded retry.
- Pool exhaustion returns a controlled service error.
- Database connection drops during commit; idempotent replay resolves outcome safely.
- Disk full and read-only filesystem do not corrupt existing local data.

## 4. Tenant and ownership integrity

The conversation schema stores `owner_id` and its queries filter by owner. This is a useful user-ownership boundary, but a tenant-aware deployment needs a first-class tenant ID and membership policy.

Required:
- Resolve tenant ID from authenticated server-side membership, never from an untrusted request field alone.
- Include tenant ID in tenant-owned rows, unique constraints, indexes, and repository query predicates.
- Ensure child rows inherit and validate the same tenant as their parent.
- Consider PostgreSQL row-level security as defense in depth, with a carefully designed connection-pool tenant context that cannot leak between borrowers.
- Use tenant-aware cache keys, job payloads, exports, audit records, and backup/restore procedures.
- Add negative tests for cross-tenant reads, writes, deletes, joins, and background jobs.

## 5. Backup, restore, and disaster recovery — P0

No end-to-end backup and restore operation was verified in this audit. A backup feature is not complete until restore has been tested.

Define:
- Recovery Point Objective (RPO) and Recovery Time Objective (RTO) per data class.
- Automated encrypted backups and retention policy.
- PostgreSQL backup method: managed snapshots and/or tested logical/physical backups, with WAL/PITR strategy where needed.
- SQLite backup method using a consistent SQLite backup mechanism or coordinated checkpoint/backup procedure; do not copy a live database file without accounting for WAL state.
- Off-host/off-account backup storage and access separation.
- Key management and key recovery procedures.
- Restore drills into an isolated environment.
- Integrity checks, row-count/sanity checks, and application-level verification after restore.
- Documented owner approval and incident communications for destructive recovery.

Suggested operational targets must be chosen by product requirements, measured in drills, and not advertised as achieved until demonstrated.

## 6. Encryption and sensitive data

- Require TLS with certificate verification for remote PostgreSQL. The repository has configuration tests rejecting remote PostgreSQL without TLS and accepting `sslmode=verify-full`.
- Use managed disk/database encryption at rest where available.
- Encrypt highly sensitive fields separately when threat model requires it.
- Store encryption keys outside the database and backups; support rotation and recovery.
- Never log connection strings, passwords, SQL parameters containing personal content, or raw message bodies.
- Define data retention, deletion, legal hold, and backup expiration policies.
- Protect local SQLite files and their backups with OS permissions and, where required, full-disk or application-level encryption.

## 7. Performance and capacity

- Derive pool sizes from the total replica count and database connection budget, not per-process defaults alone.
- Monitor pool wait time, active/idle connections, transaction duration, lock waits, query latency, rows scanned, and database storage growth.
- Add query-plan checks for owner-scoped conversation list and message pagination.
- Define per-tenant storage and request quotas.
- Bound message history reads and avoid loading unbounded conversation contents into memory.
- Use connection and statement timeouts; avoid long-lived idle transactions.
- Benchmark SQLite and PostgreSQL with representative concurrent workloads before choosing deployment defaults.

## 8. Test coverage and evidence

Inspected tests include:
- `tests/test_conversation_store.py`: owner scoping, optimistic versions, idempotent replay, pagination, and generation lifecycle.
- `tests/test_conversation_store_factory.py`: backend selection, remote PostgreSQL TLS configuration, pool bounds, and optional PostgreSQL integration.
- `tests/test_postgres_sql_translation.py`: SQL translation behavior.
- `tests/test_sqlite_file_security.py`: private file path controls.
- `tests/test_sqlite_recovery.py`: read-only recovery checks and corrupt-store preservation.

The PostgreSQL integration test is skipped unless `YUKI_TEST_POSTGRES_URL` is configured. SQL translation unit tests do not prove runtime PostgreSQL parity.

Add CI jobs for:
1. SQLite full contract suite.
2. PostgreSQL full contract suite against a disposable service.
3. Migration from each supported prior schema.
4. Concurrent transaction and lease race tests.
5. Backup/restore drill with integrity validation.
6. Fault injection: network interruption, pool exhaustion, deadlock, disk full, process termination.
7. Tenant isolation tests across API, worker, cache, and database boundaries.

## 9. Prioritized remediation

| Priority | Work item | Evidence required |
|---|---|---|
| P0 | Add versioned schema migrations | Upgrade/downgrade or forward-fix tests |
| P0 | Prove PostgreSQL contract parity | Real PostgreSQL CI suite |
| P0 | Establish backup/restore and RPO/RTO | Successful isolated restore drills |
| P0 | Enforce tenant-aware persistence | Cross-tenant negative tests |
| P1 | Add crash and concurrency fault injection | Repeatable race/failure test suite |
| P1 | Add DB and pool observability | Dashboards and alert simulations |
| P1 | Document SQLite/Windows storage security | Platform verification |
| P2 | Load-test query plans and pool sizing | Measured throughput/latency and capacity report |

## Verification status

This audit is based on targeted source and test inspection. It does not certify:
- Production database availability, TLS, encryption, or credentials.
- Live PostgreSQL transaction semantics or failover behavior.
- Migration safety or compatibility with deployed databases.
- Backup completeness or restore success.
- Cross-tenant isolation across every storage subsystem.
- Load, latency, RPO, or RTO targets.

Do not mark these controls complete until CI or operational drills produce reproducible evidence.
