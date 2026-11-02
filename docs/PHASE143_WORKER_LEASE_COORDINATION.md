# Phase 143 — Worker lease renewal coordination

## Delivered

- Expose the existing SQLite workflow-store lease renewal through `TaskRuntime.renew_lease`.
- Require a dedicated `task:lease:renew` authorization grant.
- Preserve worker ownership, lease-expiry checks, and compare-and-swap version semantics implemented by `WorkflowRunStore.renew_lease`.
- Add `AgentTaskJournal.renew_lease` so the journal's expected version advances atomically with the lease.
- Add tests for successful renewal, wrong worker, stale version, expired lease, invalid input, authorization denial, and journal version coordination.

## Operational contract

A worker that renews a lease must use the returned task version for subsequent journal events. The journal helper does this automatically. A renewal does not permit a worker to resurrect an expired lease or take over another worker's task.

## Limitations

Lease renewal is cooperative; it does not interrupt or extend a blocked provider request. Callers should renew before the lease safety margin, keep provider request timeouts below the remaining lease duration, and stop work when renewal fails. This change does not introduce automatic task replay or guarantee exactly-once external side effects.
