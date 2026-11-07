# Phase 317 — Recovery observability and incident management

## Scope

Adds a durable incident journal around lease-aware reviewer recovery. It
provides bounded attempts, cooldowns, dead-letter state, authorized inspection,
and explicit operator remediation.

## Incident lifecycle

- One incident is maintained per task, even if the task is reassigned to a
  different reviewer identity.
- Every attempt start and outcome is appended to a separate event table.
- Attempt limits and cooldowns are persisted in SQLite and enforced inside
  `BEGIN IMMEDIATE` transactions, so concurrent recovery supervisors cannot
  both consume the same eligible attempt.
- Successful lease reassignment marks the incident terminal as recovered.
- Missing/invalid replacement identities and concurrent lease/task conflicts
  enter cooldown.
- Resolver exceptions are recorded as cooldown outcomes before propagating to
  the recovery service's cycle-level error handling.
- Reaching the configured attempt limit moves the incident to dead-letter;
  subsequent attempts are denied.

## Dead-letter operations

Authorized operators can list incidents and inspect per-task event history.
Dead-letter remediation is an explicit privileged operation requiring
`review:recovery:remediate`. It resets the attempt counter, applies a bounded
new attempt limit, records the operator reason, and appends an
`operator_remediated` event. It does not itself mutate reviewer task state or
dispatch ownership; the normal lease-aware supervisor must revalidate task
eligibility before another recovery attempt.

## Security and data handling

The incident store accepts task IDs, reviewer IDs, bounded reason codes, and
timestamps only. It does not accept credentials, session tokens, lease tokens,
claim tokens, artifact contents, or raw exception messages. All read and write
operations require a host-provided authorization callback. Collection listing
uses a distinct `review:recovery:list` authorization scope.

The SQLite database should be placed on a protected local persistent volume
with restrictive filesystem permissions and monitored backup/retention. WAL
is enabled. Do not treat SQLite on an arbitrary shared network filesystem as a
multi-host coordination service.

## Integration

`ReviewerWorkerRecoverySupervisor` accepts an optional incident store.
When configured, it reserves an attempt before resolving a replacement,
honors cooldown/dead-letter decisions, and records success or deferral. Without
the store, Phase 315 behavior remains backward-compatible.

## Verification

```bash
python -m unittest tests.test_reviewer_recovery_incidents tests.test_reviewer_worker_recovery tests.test_reviewer_worker_recovery_service tests.test_reviewer_ledger_recovery -v
```

Tests cover attempt caps, cooldown, terminal success, authorization, bounded
listing, operator remediation, supervisor integration, and the existing
lease-fencing recovery path. Tests are authored; execution status must be
verified from CI or a local test run.
