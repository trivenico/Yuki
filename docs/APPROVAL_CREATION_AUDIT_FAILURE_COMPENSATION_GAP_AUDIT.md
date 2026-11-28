# Approval Creation Audit-Failure Compensation Gap Audit

## Finding

**Severity: Low — failed audit compensation can leave an unreachable pending row consuming approval capacity**

Approval creation persists a pending action in `SQLitePendingApprovalStore` before appending its `requested` event to the separate audit database. If the audit append raises, `ApprovalCoordinator._record()` attempts to remove the pending row by calling `pending_store.deny()`. That compensation is wrapped in a broad exception handler whose exception is discarded; its boolean result is also ignored.

If the pending-store delete fails or returns false while the row remains present, `_record()` still raises the generic audit failure and does not return the generated pending ID. The pending row can then remain in the database until its TTL expires, counting against the configured `max_pending` capacity.

## Source evidence

- `core/approval_flow.py`, `ApprovalCoordinator._record()`: calls `pending_store.put()` before `_audit("requested", ...)`.
- In the audit exception handler, `pending_store.deny(pending_id)` is attempted, but all exceptions are swallowed and the result is not checked.
- `core/pending_approvals.py`, `SQLitePendingApprovalStore.put()`: commits the pending row in its own SQLite transaction.
- `SQLitePendingApprovalStore.deny()`: deletes the row from the pending database and returns whether exactly one row was deleted; it can raise on SQLite errors.
- `SQLitePendingApprovalStore.put()`: counts all unexpired pending rows against `max_pending`. A stranded row therefore consumes capacity until removed or expired.

## Trigger and impact

The failure requires both the audit append and the compensating pending-row deletion to fail, or the delete to report that no row was removed while the pending row still exists. For example, an independent pending-database lock, read-only transition, or storage error can prevent compensation after the audit database has failed.

The generated pending ID is not returned to the caller because `_record()` raises. The normal approval API therefore cannot use that ID through the failed request's response. The stranded row is an orphaned capacity allocation, not an approval that the caller has been told is available.

Repeated failures can consume the bounded pending capacity and temporarily prevent new approval requests until orphaned rows expire (up to the configured pending TTL). The default composition uses a short TTL, but the constructor permits up to one hour.

This does **not** demonstrate unauthorized execution, a way to recover or guess the pending bearer ID, or audit bypass. It is a cross-store compensation and availability gap.

## Recommended remediation

1. Check the result of `pending_store.deny()`; treat a false result as failed compensation unless the store can prove the row is already absent.
2. Do not silently discard cleanup failures. Preserve the original audit exception while attaching/logging a bounded compensation-failure status that does not expose the pending bearer ID.
3. Add a durable recovery marker or startup reconciliation that can identify pending rows without corresponding `requested` audit events. Use a stable, non-secret correlation digest shared across stores; do not expose raw pending IDs.
4. Consider a small outbox/state-machine design so pending creation and audit intent can be reconciled deterministically across the two SQLite databases.
5. Add regression tests where the audit append fails and `deny()` raises, returns false while leaving the row, or succeeds. Assert that failure is observable and that orphaned rows are detectable and reclaimed safely.
6. Keep normal approval fail-closed: never make an orphaned pending row executable merely because its audit event is absent.

## Review limitations

Source-only review at PR #1346 head `8cbdcea0ee0e3af658719b122c4e742de775f1f7`. No failure injection, database lock simulation, runtime execution, tests, or CI were run. The finding describes the explicit compensation control flow and its failure handling; it was not reproduced dynamically.
