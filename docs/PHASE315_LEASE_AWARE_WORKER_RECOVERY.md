# Phase 315 — Lease-aware reviewer worker recovery

## Scope

Adds a recovery supervisor that reconciles expired worker registrations with
expired in-progress reviewer task leases. It does not infer task safety from a
worker heartbeat alone.

## Recovery protocol

1. Expire stale worker registrations using the durable worker registry.
2. Identify reviewer identities with expired worker instances.
3. Ask the task ledger only for in-progress tasks whose reviewer task lease is
   already expired.
4. Resolve a distinct replacement reviewer through a trusted host callback.
5. Call the ledger's atomic `recover_in_progress` transition with the observed
   task version. The ledger verifies lease expiry under its write transaction,
   increments the lease generation, changes reviewer identity, appends
   abandonment/reassignment audit events, and requeues the existing dispatch
   while invalidating its prior outbox claim token.
6. If task version or lease state changed concurrently, defer rather than force
   a stale transition.

Tasks whose worker expired but whose task lease remains active are not
recovered. Missing replacements are deferred. Submitted tasks are not in the
recovery query. The supervisor never receives reviewer credentials or lease
tokens.

## Authority boundaries

Worker registry state is a liveness signal. ReviewerTaskLedger and its
ReviewerLeaseAuthority remain the authority for task state, lease expiry,
generation fencing, and reassignment. The ledger's atomic recovery operation
is the only component that may rotate a task lease. Replacement selection is
host supplied and must return an identity authorized by the ledger's
authorization policy.

## Dispatch correctness

The task, lease, audit events, and outbox dispatch reassignment are updated
inside one SQLite transaction. Existing dispatch attempts are retained for
audit, but claim ownership, token digest, and expiry are cleared; the dispatch
becomes pending for the replacement reviewer. A stale worker cannot complete
the old dispatch with its previous claim token.

## Verification

Run:

```bash
python -m unittest tests.test_reviewer_worker_recovery tests.test_reviewer_ledger_recovery tests.test_reviewer_ledger -v
```

Tests cover eligible recovery, no-op behavior without an expired worker,
deferral when no replacement exists, concurrent recovery races, and an
end-to-end ledger test that verifies lease-generation rotation, audit events,
and dispatch requeue for the replacement reviewer. Tests are authored, not claimed as
executed unless CI evidence confirms execution.
