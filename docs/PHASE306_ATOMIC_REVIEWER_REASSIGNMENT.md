# Phase 306 — Atomic reviewer reassignment

## Scope

Adds a ledger-controlled reassignment transition for tasks that remain
unclaimed in the `assigned` state. Reassignment requires a configured lease
authority and the caller's `review:reassign` permission.

## Atomic operation

The ledger starts `BEGIN IMMEDIATE` and then:
1. Verifies task state/version and replacement reviewer.
2. Calls lease recovery using the caller-owned transaction.
3. Requires the prior lease to be expired.
4. Increments the lease generation and issues a replacement token.
5. Updates the task's current reviewer and version.
6. Appends a `reassigned` lifecycle event.

The lease row, task row, and event are committed or rolled back together.
The old token is fenced by the changed lease nonce and generation. The old
reviewer is also rejected by the ledger's current reviewer identity check.

## Deliberate limitation

This phase does not reassign `in_progress` tasks. The current event model
assumes a single claim transition; safely representing an abandoned claim,
reassignment, and replacement claim requires a fuller lifecycle schema and
history migration. The API therefore fails closed unless the task is still
`assigned`. It does not revoke the previous reviewer's general session:
that session is artifact-scoped, and task-level identity plus lease fencing
blocks its use on the reassigned task.

A replacement reviewer must receive a fresh host-issued session before
execution. The caller must also deliver the returned lease token only through
the trusted worker transport.

## Tests

Regression tests cover expired-lease reassignment, task/event consistency,
old-token invalidation, and rejection of active or in-progress assignments.
Tests are authored but not executed.
