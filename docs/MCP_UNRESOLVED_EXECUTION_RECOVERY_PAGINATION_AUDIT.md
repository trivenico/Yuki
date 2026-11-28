# MCP Unresolved Execution Recovery Pagination Audit

## Finding

**Severity: Low — unresolved execution records beyond the fixed result cap are not retrievable through the recovery API**

`SQLiteApprovalAuditLog.list_unresolved_executions()` accepts a bounded `limit` from 1 to 1,000, orders unresolved executions by descending event ID, and returns only the first matching rows. It exposes no cursor, offset, starting event ID, or ascending-order option.

When more than 1,000 execution-start records remain unresolved, each call returns the same newest 1,000 entries. Older unresolved executions are permanently hidden behind that result window through this method; resolving newer entries may eventually expose older ones, but there is no deterministic way to enumerate or prioritize the full backlog.

## Source evidence

- `core/approval_audit.py`, `SQLiteApprovalAuditLog.list_unresolved_executions()`:
  - rejects limits above 1,000;
  - filters `execution_started` rows without a later terminal event;
  - orders by `started.event_id DESC`;
  - applies only `LIMIT ?`, with no pagination cursor or offset.
- `tests/test_approval_audit.py` covers the upper bound on `list_events()`, but does not exercise unresolved-execution pagination or a backlog larger than the maximum result size.
- The returned data contains only hashes and the start event ID, so an operator cannot safely reconstruct omitted entries from another exposed field in the response.

## Impact and boundaries

If an incident, outage, or repeated audit/transport failure creates more than 1,000 unresolved executions, operators using this API cannot enumerate the full set. Older pending operations may remain unreconciled indefinitely, and operational dashboards or recovery automation may report an incomplete backlog without an explicit truncation signal.

This does **not** create a tool-dispatch authorization bypass, delete audit records, or cause automatic retries. The issue is bounded recovery visibility and operational completeness. The risk is most relevant to high-volume deployments or prolonged remote-service outages.

## Recommended remediation

1. Add stable keyset pagination using the last returned `event_id` (or an explicit before/after cursor) while preserving deterministic ordering.
2. Return pagination metadata such as `has_more` and `next_cursor`; never silently present a capped result as the complete unresolved set.
3. Consider an ascending recovery mode so the oldest unresolved operations can be prioritized first.
4. Keep chain verification and the query snapshot consistent, ideally reading verification and results under one SQLite read transaction.
5. Add tests that create more than 1,000 unresolved executions and prove every record can be enumerated exactly once across pages, including while new events are appended.
6. Ensure management interfaces and recovery jobs continue until `has_more` is false rather than treating the first page as a complete inventory.

Do not solve this by simply raising the maximum limit: the audit table is unbounded, so any fixed cap can eventually be exceeded.

## Review limitations

Source-only review at PR #1346 head `ce744cf4cd9d123ee465adf6e361c28f109c039c`. No high-volume database test, runtime recovery exercise, or CI was executed. This is a code-path completeness finding, not an observed production backlog.
