# MCP Approval Audit Concurrent Schema Migration Race Audit

## Finding

**Severity: Low — concurrent process initialization can fail during the legacy audit schema upgrade**

`SQLiteApprovalAuditLog.__init__()` inspects `PRAGMA table_info(approval_audit_events)` to decide whether to add `pending_id_hash`. That inspection happens before the migration's `BEGIN IMMEDIATE` transaction. If two processes initialize the same legacy database concurrently, both can observe that the column is absent. The first process acquires the write lock and completes the migration. The second then acquires the lock and executes the stale migration decision, attempting to add a column that already exists.

SQLite serializes the writers, but serialization alone does not refresh the earlier schema decision. The second constructor can therefore raise a duplicate-column `sqlite3.OperationalError`.

## Source evidence

- `core/approval_audit.py`, `SQLiteApprovalAuditLog.__init__()`: creates the table if absent, reads the column list through `PRAGMA table_info`, and conditionally enters `BEGIN IMMEDIATE` only after the schema check.
- The migration then runs `ALTER TABLE approval_audit_events ADD COLUMN pending_id_hash TEXT`, updates legacy rows, rebuilds the hash chain, and commits.
- The schema inspection is not repeated after obtaining the write lock, so a process waiting behind another migrator continues using a stale `columns` set.
- `_connect()` configures a five-second SQLite busy timeout, which permits the second process to wait for the first writer but does not make its pre-lock schema observation current.

## Trigger and impact

The issue requires a legacy database that lacks `pending_id_hash` and overlapping initialization by multiple processes. This is plausible where desktop/API/worker processes share one profile and initialize the approval subsystem during startup.

One process can complete the migration; another can fail to construct `SQLiteApprovalAuditLog` with a duplicate-column error. The failed process may not start the approval/MCP functionality that depends on this audit store until it is restarted or retries initialization.

The migration's explicit transaction and rollback protect the data from a partial migration in this scenario. No evidence was found that the race corrupts existing rows or creates a partially rebuilt chain. This is a startup availability defect, not an authorization bypass.

## Recommended remediation

1. Begin the migration write transaction before reading the schema version/column set.
2. Re-read `PRAGMA table_info(approval_audit_events)` after `BEGIN IMMEDIATE` has acquired the writer reservation.
3. Perform the `ALTER TABLE`, legacy-row backfill, and chain rebuild only if the column is still absent inside that transaction.
4. Prefer a durable schema-version table or explicit migration ledger as migrations expand; make each migration idempotent under serialized execution.
5. Add a multi-process regression test using two independent connections/processes synchronized so both attempt legacy initialization. Assert both constructors complete, the column exists once, all legacy rows are preserved, and `verify_chain()` succeeds.
6. Include crash/interruption coverage around the migration transaction to confirm SQLite rolls back the schema and data changes atomically.

Do not treat a process-local mutex as a fix: the relevant contention is across independent processes and SQLite connections.

## Review limitations

Source-only review at PR #1346 head `e7ad0bea29b42e4f2ea041a84ba5849524574168`. No concurrent-process test, migration failure injection, runtime database manipulation, tests, or CI were executed. The race follows from the observed ordering of the schema inspection and write transaction; it was not reproduced dynamically.
