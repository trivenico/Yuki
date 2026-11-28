# Approval Recovery Snapshot Consistency Audit

## Finding

**Severity: Low — recovery diagnostics can report a mixed-time snapshot when the shared maintenance fence is omitted**

The approval recovery scanner reads the pending store, grant store, audit chain, audit event list, and unresolved executions in separate operations. Each store's individual read acquires and releases its own maintenance-fence lease. However, `scan_approval_recovery()` only holds one encompassing lease when the caller explicitly supplies `maintenance_fence`.

The production composition gives the pending, audit, and grant stores the same cross-process `MaintenanceFence`, but the scanner does not infer that shared fence from the stores. If a caller omits the optional argument—as the public function signature permits—the scan is not a consistent cross-store snapshot. Another process can complete a legitimate state transition between individual reads.

## Source evidence

- `core/approval_composition.py`: constructs pending, audit, and grant stores with the same `maintenance_fence`.
- `core/approval_recovery.py`, `scan_approval_recovery()`: the outer `with maintenance_fence.lease()` is conditional on a non-None argument; otherwise it calls the scan directly.
- `_scan_approval_recovery_unfenced()`: obtains pending metadata, grant metadata, verifies the audit chain, lists audit events, and lists unresolved executions as separate calls.
- Each store wraps its own read operation in `self._lease()`, which releases the cross-process lock when that individual call returns.
- `ApprovalCoordinator.record()` and `approve()` also use the shared fence, so a scanner that omits the outer lease can interleave with those multi-store transitions.

## Example

During approval creation:
1. The pending row is committed.
2. The scanner reads pending metadata and sees that row.
3. The coordinator appends the `requested` event.
4. The scanner reads audit events and sees the event.

This sequence is benign. But if the audit append occurs just after the scanner's audit-event read, the scanner can report an `orphan_pending_snapshot` from a mixed-time view even though the operation completes successfully moments later.

During approval consumption:
1. The coordinator atomically consumes the pending row.
2. The scanner reads pending metadata and sees no row.
3. The coordinator appends `approved`.
4. The scanner reads audit events before that append, and can report `consumed_without_approval_decision`.

These are transient false-positive recovery findings, not approval bypasses or evidence of corrupt durable state. The scanner may also miss a transition whose related records fall on opposite sides of its read boundaries.

## Impact and boundaries

A recovery operator may investigate or attempt manual remediation for an operation that is still completing normally. The report can also be marked complete even though it did not represent one point-in-time view of the three databases. The risk is diagnostic correctness and operator workload; no state is modified by the scanner itself.

When the caller passes the common fence, the scanner holds the exclusive cross-process lease over the entire read sequence and this interleaving is prevented for participating operations.

## Recommended remediation

1. Infer the shared fence from `pending_store`, `audit_log`, and `grant_store` when the argument is omitted, as `ApprovalCoordinator` already does for its stores.
2. Require all non-None store fences to be the same object; reject mixed or missing fences in production recovery composition.
3. Alternatively, make the fence mandatory for production scans and expose a separate explicitly named unfenced diagnostic mode whose report is marked non-atomic/incomplete.
4. Add a concurrency regression test that pauses a record/approve operation between its database writes and verifies the scanner cannot observe an intermediate state when using the production composition.
5. Keep the scanner read-only; do not auto-delete or retry records based on a mixed-time diagnostic result.

## Review limitations

Source-only review at PR #1346 head `c551439c3460e657e7894267bcb52aeaff682586`. No concurrent process test, runtime execution, tests, or CI were run. The false-positive interleavings follow from the explicit per-call lease boundaries; they were not reproduced dynamically.
