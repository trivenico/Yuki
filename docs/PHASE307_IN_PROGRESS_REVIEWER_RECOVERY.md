# Phase 307 — In-progress reviewer crash recovery

## Scope

Extends the reviewer ledger lifecycle to represent abandoned work and replay
multiple reviewer reassignments. The host can recover an expired in-progress
task in one SQLite transaction.

## Recovery transaction

`recover_in_progress` requires both `review:abandon` and
`review:reassign` permissions. It validates the current task state/version,
requires the prior lease to have expired, rotates the lease generation and
token, updates the reviewer identity, and appends two ordered events:
`abandoned` followed by `reassigned`. The task, lease, lease audit event,
and task lifecycle events commit or roll back together.

A stale reviewer is fenced by all three checks: current ledger reviewer
identity, lease generation/nonce, and expected task version. A late submission
from the previous reviewer therefore cannot alter the recovered task.

## Lifecycle integrity

The ledger now replays the full event sequence rather than assuming a single
claim/reassignment. It supports repeated reassignment, claim, abandonment,
and replacement claim cycles while checking reviewer identity transitions,
event payload schemas, event sequence, state projection, signed result digest,
and reviewer signature.

## Separate abandon operation

`abandon` can mark an in-progress task abandoned only after its lease expires.
The task remains assigned to its previous reviewer until an authorized
reassignment. The preferred recovery operation is `recover_in_progress`,
which combines both transitions atomically.

## Boundaries

- Submitted tasks are immutable and cannot be recovered.
- Active leases cannot be forcibly reclaimed through this API.
- Recovery does not execute the replacement reviewer or mint its reviewer
  session. The host must issue a fresh session and securely deliver the new
  lease token.
- The existing reviewer session is artifact-scoped; task-level reviewer
  identity and lease fencing prevent its old holder from submitting to the
  reassigned task.
- Tests are authored but not executed.
