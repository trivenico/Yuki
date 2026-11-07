# Phase 303 — Reviewer lifecycle supervision

## Scope

Adds an authorized, read-only supervisor that inspects bounded batches through
`ReviewerTaskLedger.get`. Every read therefore retains the ledger's access
control and payload/signature/event integrity verification.

## Behavior

- Counts assigned, in-progress, and submitted tasks.
- Detects old in-progress claims using a configurable age threshold.
- Reports a recommended operator action for stale work.
- Rejects duplicate IDs, excessive batches, invalid thresholds, and naive
  timestamps.
- Does not mutate tasks, reassign reviewers, retry execution, or mark reviews
  complete.

## Recovery safety

The current ledger state machine has only assigned, in_progress, and submitted
states and no lease owner/expiry or cancellation transition. This phase
intentionally reports stale work rather than automatically reclaiming it.
Automatic recovery must follow a ledger migration with lease tokens, monotonic
claim generations, cancellation/revocation semantics, and audited transitions
so a late worker cannot submit after a replacement reviewer has claimed work.

## Verification

Focused tests cover stale detection, lifecycle counts, duplicate identifiers,
and timezone-aware inspection. Tests are authored but not executed.
