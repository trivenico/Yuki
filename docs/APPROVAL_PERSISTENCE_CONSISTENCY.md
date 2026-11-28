# Approval persistence consistency contract

## Scope

The approval lifecycle spans three independent SQLite databases:

- pending approval snapshots;
- owner approval audit events;
- one-shot exact-action grants.

SQLite transactions make each individual database operation atomic. They do **not** make writes across these databases atomic. Yuki must not describe this workflow as exactly-once or crash-atomic.

## Shared maintenance fence

Production composition must create one `MaintenanceFence` rooted in the private Yuki profile and inject that same fence into:

1. `SQLitePendingApprovalStore`;
2. `SQLiteApprovalAuditLog`;
3. `SQLiteToolApprovalStore`;
4. `ApprovalCoordinator`;
5. `GuardedApprovalExecutor` indirectly through the matching audit and grant stores.

The coordinator holds the fence for the complete record/approve lifecycle. Store methods also acquire it before opening SQLite. The fence is re-entrant for nested calls and uses an OS lock to serialize cooperating processes. Composition rejects mismatched fence instances between participating stores.

This is a serialization boundary, not a distributed transaction. Code that opens these databases directly or constructs stores without the shared fence is outside the guarantee.

## Crash windows and safe interpretation

| Crash point | Durable state that may remain | Required interpretation |
| --- | --- | --- |
| After pending snapshot insert, before `requested` audit append | Orphan pending row | Never infer owner approval from its presence. No automatic dispatch. |
| After pending snapshot is consumed, before `approved` audit append | Request may be gone; only `requested` event remains | Treat as consumed/abandoned. The read-only scanner now reports `consumed_without_approval_decision` when no matching pending row or later decision/dispatch event exists. Do not recreate or retry automatically. |
| After `approved`, before `execution_started` | Approval evidence, no dispatch-start evidence | No automatic retry; require explicit owner review. |
| After `execution_started`, before handler returns | Started event without a determinate terminal event | Reconciliation required. Target may or may not have acted. |
| After handler side effect, before `executed` audit append | Started event; target may have acted | Outcome unknown. Verify the target independently; never replay automatically. |
| After one-shot grant insert, before token consumption | Grant row may remain until expiry; bearer token may be lost on process crash | No automatic reuse. Let the short-lived grant expire or revoke it through a controlled maintenance procedure. |
| After grant consumption, before policy audit append | Grant is consumed; handler is not authorized to run | Fail closed. Do not restore the grant. |
| After policy audit append, before handler dispatch | Approval evidence exists; handler may not have started | Keep the coordinator execution-start record open for owner reconciliation. |

## Operational invariants

- A pending row is not proof of approval.
- An audit event is not proof that an external side effect completed.
- A consumed grant is never restored after an audit or dispatch error.
- `outcome_unknown` remains in the reconciliation queue.
- `not_executed` reconciliation is a recorded finding, not permission to retry.
- Recovery tooling only inspects and records findings; it never dispatches an action.
- The hash chain detects accidental or unsophisticated tampering, but it is not a keyed signature or independently controlled immutable archive.

## Remaining work before enabling consequential tools

- Extend the explicit owner-run read-only consistency scan to detect unmatched lifecycle events. It now reports orphan pending snapshots, consumed requests missing a decision event, approved requests missing a dispatch marker, expired grants, and unresolved dispatches. The scan is not yet wired into every process startup.
- Add fault-injection tests at every crash boundary above; current coverage is diagnostic-focused and does not yet simulate process termination at every persistence boundary.
- Add idempotency keys and target-side reconciliation where the external system supports them.
- Consider consolidating lifecycle state into one transactional database or an outbox/inbox design.
- Send audit checkpoints to a separately controlled append-only destination before treating the log as high-assurance evidence.
- Keep all real host-side tools disabled until these checks and deployment acceptance tests pass.

## Read-only recovery scan

The current implementation exposes `scan_approval_recovery()` and the owner-authenticated CLI command:

```bash
yuki auth consistency-check \
  --pending-db /path/to/pending-approvals.sqlite3 \
  --audit-db /path/to/approval-audit.sqlite3 \
  --grants-db /path/to/tool-approvals.sqlite3
```

The command requires the existing three database files and the local owner administrator login. It opens each database through SQLite `mode=ro` / `query_only` readers and acquires the same private-profile maintenance fence for the entire scan. It never creates missing databases or initializes schemas.

Exit status is zero only when the audit chain is valid and the bounded scan is complete; status 3 means an invalid audit chain or incomplete report; status 2 means inspection could not be performed. Findings contain hashes and limited metadata, not bearer tokens or raw action arguments.

This is an explicit owner-run preflight, not yet an automatic hook in every Yuki process startup. Do not enable consequential tools merely because the scan is clean; the remaining crash-injection, idempotency, and independent audit-checkpoint work still applies.

## Unified-ledger design proposal

A detailed, non-operative target architecture is documented in
[UNIFIED_APPROVAL_LEDGER_OUTBOX_DESIGN.md](UNIFIED_APPROVAL_LEDGER_OUTBOX_DESIGN.md).
It proposes one SQLite transaction boundary for request state, audit events,
and dispatch intent, while explicitly preserving the uncertainty boundary
around external side effects.

This is a design proposal only. The current three-store runtime is unchanged.
No schema migration, dual-write, dispatch worker, or enablement change is
authorized by that document. Existing consequential execution remains
disabled pending implementation, migration review, crash/concurrency tests,
target-idempotency contracts, and verified backup/restore.
