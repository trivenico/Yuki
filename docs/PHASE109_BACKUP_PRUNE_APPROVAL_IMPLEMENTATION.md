# Phase 109 — backup-pruning approval implementation

## Implemented in this checkpoint

- `core/approved_sqlite_backup_pruning.py` introduces `ApprovedSQLiteBackupPruner`.
- Requires an existing `ToolPrincipal`, trusted-principal callback, `backup:prune` permission, and bounded authenticated session ID.
- Uses `SQLitePendingApprovalStore` for actor/session-bound, expiring, one-time approval snapshots.
- Uses `SQLiteApprovalAuditLog` for requested, approved, denied, execution-started, executed, and execution-failed lifecycle events.
- Binds approval to a SHA-256 operation fingerprint covering backup-root identity, workspace identity, explicit database inventory identities, retention settings, pinned/verified/retained/eligible IDs, exact approved IDs, and every verified manifest hash.
- Recomputes the fingerprint after consuming the approval and immediately before invoking Phase 108.
- Passes only the approved set IDs to the existing pruning primitive.
- Distinguishes post-operation audit failure using `BackupPruneOutcomeUnknown`; callers must not automatically retry an unknown outcome.

## Tests added

`tests/test_approved_sqlite_backup_pruning.py` covers successful pruning and audit ordering, principal/permission rejection, denied approval, actor/session binding, single-use consumption, manifest mutation invalidation, and failure to persist the initial audit event.

## Important limitations / next review

- This is a library-level orchestration service; it is not yet wired into an API, UI, agent tool registry, or production runtime composition.
- The `approve_operation` callback must be connected to a real authenticated operator approval surface. A callback returning `True` is not itself proof of a human action.
- Pending approvals and audit storage must be deployed on private, durable paths outside the backup root. Their service-account ownership and backup/restore strategy need deployment review.
- The operation fingerprint is a consistency binding, not a filesystem lock. A same-service-account concurrent attacker remains outside the current threat boundary; Phase 108's filesystem checks remain mandatory.
- The hash chain is not an external immutable trust anchor and cannot protect against an attacker who can rewrite the audit database and recompute the chain.
- Tests are committed but have not yet been independently executed in CI or a local checkout. Do not treat this phase as production-ready until test execution, adversarial review, and runtime integration are complete.
