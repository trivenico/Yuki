# MCP Approval Audit Storage Growth and Retention Audit

## Finding

**Severity: Low — the durable MCP/approval audit store has no retention, archival, or capacity policy**

`SQLiteApprovalAuditLog` appends every policy decision and execution lifecycle event to `approval_audit_events`. The table has no retention metadata, partitioning, archival process, bounded capacity, or supported pruning operation. The store therefore grows for the lifetime of the profile.

The audit path is created by `build_approval_services()` as `approval-audit.sqlite3` under the private profile root. The same audit log is used by approval coordination and guarded execution. MCP policy and execution adapters also append to this store when composed with it. There is no store-specific backup, archival, or retention lifecycle in the inspected composition.

## Source evidence

- `core/approval_audit.py`, `SQLiteApprovalAuditLog.__init__()`: creates the `approval_audit_events` table and an index on creation time/event ID, but no retention or capacity controls.
- `SQLiteApprovalAuditLog.append()`: performs an insert for each event under `BEGIN IMMEDIATE`; no size threshold, quota, archival, or pruning check is present.
- `MCPExecutionAudit.__call__()` and `MCPToolPolicyAudit.__call__()`: route execution and policy lifecycle events into the same append-only log.
- `core/approval_composition.py`, `build_approval_services()`: places `approval-audit.sqlite3` in the profile root and injects it into approval coordination and guarded execution.
- `docs/DAILY_BACKUP.md`: the general data-directory archive explicitly does not guarantee application-specific database transaction consistency and recommends stopping Yuki before backup.
- `docs/PERSISTENT_STORE_INVENTORY_AUDIT.md`: identifies the audit database as a separate persistent store with no coordinated multi-store backup hook or global maintenance integration.

## Impact and boundaries

Long-running or high-volume deployments accumulate rows indefinitely. Repeated owner policy mutations and tool execution events can grow the database until the filesystem has insufficient space for new writes. Since policy audit is written before policy mutation and execution-start audit is written before remote dispatch, append failure blocks those operations. This is fail-closed behavior, but it can make MCP policy administration and dispatch unavailable until storage is recovered.

The absence of retention also makes backup archives grow over time and leaves operators without a documented policy for retention periods, legal holds, archival verification, or secure disposal.

This does **not** show a remote unauthenticated write path, an authorization bypass, or a failure to block dispatch when the initial audit append fails. No disk-exhaustion incident was observed. The finding concerns unbounded persistent growth and missing operational lifecycle controls.

## Recommended remediation

1. Define an explicit retention and archival policy for each audit event class, including compliance/legal-hold requirements and owner-visible retention settings.
2. Do not delete events from the active hash chain in place. Prefer signed, independently stored archive segments with verifiable boundary checkpoints, then retain a compact active segment.
3. Add capacity monitoring and alerts with sufficient headroom to archive or expand storage before audit writes fail.
4. Make archive publication atomic and verify archived event counts, event ID ranges, hashes, and chain continuity before pruning any active data.
5. Integrate the audit database into a coordinated, SQLite-aware backup/restore procedure. Back up after a safe checkpoint or through SQLite's online backup API; do not copy only the main database file while WAL activity may be present.
6. Add tests for archive interruption, restore verification, retention boundary behavior, disk-full/SQLITE_FULL handling, and fail-closed dispatch when the pre-dispatch audit write cannot be persisted.
7. Document recovery steps for a full or read-only filesystem, preserving unresolved execution records and avoiding unsafe manual deletion.

Any archival/pruning design must preserve the ability to verify historical records and reconcile unresolved executions. A fixed row cap or blind deletion is not a safe retention strategy.

## Review limitations

Source-only review at PR #1346 head `7794f3381de38085e6642aeda4902ce4b76524f9`. No database load test, disk-full simulation, backup/restore exercise, tests, or CI were executed. The finding is based on the absence of retention/capacity controls in the inspected implementation and composition, not an observed production outage.
