# Approval Recovery Truncated-History Correlation Audit

## Finding

**Severity: Low — a bounded audit-event window can produce false orphan-pending findings**

The recovery scanner correlates pending rows against only the newest `limit` audit events. When the audit history exceeds that limit, an active pending row whose `requested` event is older than the returned window is classified as `orphan_pending_snapshot`, even though its request event may exist in the omitted history.

The scanner does add a warning and marks the report incomplete when the event count reaches the limit. However, it still emits the orphan finding from the truncated window. Consumers that act on findings without treating `complete=False` as a hard qualification can mistake ordinary older pending requests for storage corruption or failed audit compensation.

## Source evidence

- `core/approval_recovery.py`, `_scan_approval_recovery_unfenced()` calls `audit_log.list_events(limit=limit)`.
- `SQLiteApprovalAuditLog.list_events()` returns the newest events first, ordered by descending `event_id`, with a fixed upper bound of 10,000.
- The scanner adds a warning if `len(events) == limit`, but continues correlation using only that bounded event list.
- `known_requested` is built exclusively from the returned event window.
- Every pending row whose hash is absent from `known_requested` is emitted as `orphan_pending_snapshot`; the classification does not distinguish “not present in scanned history” from “proven absent from full history.”
- The report's `complete` field becomes false, but the individual finding has no completeness/uncertainty field.

## Example

If the audit database contains more than 10,000 events and a still-live pending request's `requested` event is older than the newest 10,000, then:
1. The pending-store query can return that live pending row.
2. The audit query omits its older `requested` event.
3. `known_requested` does not contain the row's pending hash.
4. The scanner emits `orphan_pending_snapshot` despite the event existing in the full audit history.

This is particularly plausible in a high-volume deployment because pending rows can remain live for minutes while many unrelated audit events are appended.

## Impact and boundaries

The issue is diagnostic precision, not approval authorization. The scanner is read-only, and the finding does not demonstrate that a pending request can be executed without audit. The top-level incomplete warning is a useful safeguard, but callers must interpret it correctly; a finding that depends on omitted history should not be presented as a confirmed orphan.

This is distinct from the existing unresolved-execution pagination finding: that report concerns the fixed 1,000-row unresolved-dispatch listing. This finding concerns the scanner's 10,000-event correlation window and the resulting false-positive orphan classification.

## Recommended remediation

1. When the audit-event listing reaches its limit, do not emit definitive `orphan_pending_snapshot` findings based solely on absence from that window. Emit an explicitly indeterminate finding or suppress that classification until the full relevant history is available.
2. Add a dedicated indexed audit lookup by `pending_id_hash` for each pending row, executed under the same maintenance fence, or implement keyset pagination to cover the complete relevant event history.
3. Include a per-finding completeness/coverage indicator so downstream dashboards cannot mistake a window-limited absence for proof of orphaning.
4. Keep the report-level `complete=False` behavior and require consumers to surface warnings alongside findings.
5. Add a regression test with more than 10,000 audit events and a pending row whose `requested` event falls just outside the returned newest-first window.

## Review limitations

Source-only review at PR #1346 head `52d92f1b0f9834af1785748bb43082ef58ac785c`. No high-volume database fixture, runtime execution, tests, or CI were run. The classification follows directly from the bounded query and correlation logic; it was not reproduced dynamically.
