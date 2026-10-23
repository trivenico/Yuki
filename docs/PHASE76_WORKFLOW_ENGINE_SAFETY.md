# Phase 76 — workflow engine safety and orchestration boundary

## Delivered in this phase

- Validate all steps before invoking any handler, so a malformed later step cannot follow earlier side effects.
- Reject unknown mapping keys, NUL-containing values, overlong names/arguments, and duplicate step names.
- Keep the action surface limited to `note`, `emit`, and `wait_for_approval`.
- Require an exact boolean `True` from the approval callback; an approval-gate step cannot disable its own approval.
- Limit handler output and return redacted error categories rather than exception text.
- Add regression tests for validation, approval denial, bounded output, handler failures and workflow limits.

## Explicit limitations

This runner remains synchronous and in-process. It does not persist workflow state, schedule jobs, provide retries/cancellation, execute arbitrary connectors, or guarantee that a synchronous handler will return before its own deadline. A durable queue/scheduler must be a separate adapter with idempotency, lease recovery, actor-bound approval, deadlines, audit receipts and per-step budgets. Do not run untrusted repository commands in this engine.

## Validation

The dedicated workflow runs the standard-library unit tests. Passing these tests is not evidence of a production queue, external integration, or deployment readiness.
