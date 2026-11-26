# Cross-process maintenance fence implementation contract

**Status:** Design only; no lock implementation is enabled.
**Review date:** 2026-11-26
**Applies to:** Private owner-operated Linux-first deployment, with Windows/macOS client compatibility.

## Security objective

A control-plane migration must not race with an authentication, workflow, desktop, CLI, scheduler, recovery, or worker write. The fence is a coordination primitive, not an authorization system and not a substitute for stopping in-flight external effects.

The required invariant is:

> Every process that can mutate either source database holds a shared maintenance lease from before its first state-dependent read until after its SQLite transaction commits or rolls back. The migration process holds the exclusive lease for the entire preflight recheck, snapshot, merge, validation, and configuration cutover.

A migration-only lock does not satisfy this invariant. A process that does not participate can still write around the lock.

## Lock primitive

Use one persistent lock file beneath the already-validated private profile root. Its path is derived from the canonical profile root, not from the workspace or either database path.

- POSIX (Linux/macOS): use kernel advisory flock shared locks for ordinary operations and an exclusive lock for migration.
- Windows: use a byte-range OS file lock. If the selected primitive cannot provide shared locks reliably, serialize ordinary operations with exclusive locks; correctness takes priority over concurrency.
- Add an in-process per-path reader/writer coordinator so threads in one process obey the same ordering and nested service calls do not deadlock.
- The OS lock is authoritative across processes. Process IDs, host names, timestamps, and diagnostic owner records are informational only.
- Never delete/recreate the lock file to release a lock. Keep the inode/file identity stable; the kernel releases a held lock when the owning process closes the descriptor or exits.
- Never break a lock because a PID appears absent. PID reuse, namespaces, remote hosts, and delayed process shutdown make PID-based stale-lock recovery unsafe.
- Reject symlink/reparse-point lock paths, non-regular lock files, unexpected ownership/permissions, and lock roots that cannot be verified private. Fail closed on unsupported filesystems or platforms rather than silently falling back to a process-local mutex.
- Do not put secrets, bearer tokens, database content, or task payloads in lock metadata.

A lock timeout returns a typed maintenance-busy error. It must not proceed without a lease. Timeout and cancellation release any partially acquired in-process and OS lock state.

## Required lease boundaries

The shared lease must enclose the complete state mutation, not merely a preflight check.

| Writer surface | Required protected operations |
|---|---|
| BuiltInAuthService | account creation/bootstrap, login counters and session issuance, session revocation, user disable, and any future password/role/session mutation |
| WorkflowRunStore | create, approve, claim, renew, transition, cancel, recovery, event append, approval-session revocation, and all schema/data writes |
| Task runtime / retry helpers | complete operation including source-state validation, retry creation, and audit event transaction |
| Desktop | every background worker and controller path that reaches an auth/workflow mutation; shutdown must wait for operations to unwind |
| CLI | every command that mutates identity, session, workflow, task, approval, recovery, or configuration state |
| Specialist worker / scheduler | claim through terminal persistence, including lease renewal and recovery reconciliation |
| Migration utility | exclusive lease before its final inventory; hold through snapshots, destination construction, validation, and atomic config switch |

Read-only operations need not block migration only if they cannot mutate schema, trigger lazy initialization, create directories, checkpoint WAL, or otherwise write. Current constructors create/migrate schema and set journal mode, so constructor/startup paths must be classified as writers until proven otherwise.

## Lock ordering and transaction discipline

1. Acquire the maintenance lease.
2. Open the SQLite connection and configure busy timeout, foreign keys, and synchronous policy.
3. Begin the SQLite transaction (BEGIN IMMEDIATE for read-modify-write paths).
4. Perform bounded local reads and writes.
5. Commit or roll back.
6. Close the connection.
7. Release the maintenance lease.

Never acquire the maintenance lease after beginning a SQLite transaction. Never call a provider, plugin, browser, shell, network API, or long-running filesystem operation while holding a SQLite write transaction. Do not hold a shared lease around unbounded provider work: persist a bounded task state, release the lease, execute outside the transaction, then reacquire a shared lease and validate the current lease/fencing generation before committing the result.

The migration exclusive lease prevents new participating writers. It does not prove that a previously dispatched handler has stopped or that an external side effect has completed. Migration must separately stop dispatch, drain/cancel workers, inspect task state and external-operation reconciliation, and refuse cutover while unresolved effects remain.

## Rollout plan

1. Introduce a private-profile lock-path abstraction and platform-specific lock implementation with no implicit process-local fallback.
2. Add the lease to auth and workflow store constructors/connection factories. Schema initialization and journal-mode changes are writes and must be covered.
3. Add explicit shared-lease coverage to every public mutator and internal helper that opens a write transaction. Avoid double-acquiring through nested calls by using a documented reentrant lease scope.
4. Make desktop, CLI, scheduler, recovery and standalone worker composition inject the same lock authority derived from the same canonical profile root. Reject mismatched lock roots and database paths at startup.
5. Add a startup capability report listing every registered writer surface and its fence implementation. Migration refuses unless the required writer registry is complete; this report is a completeness check, not a substitute for code-level enforcement.
6. Only after every writer path participates, add the exclusive migration command. It must acquire exclusivity, re-inventory the source DBs, verify source file identities/schema fingerprints, and then use SQLite's backup API.
7. Keep the old databases untouched and retain backups. Cutover configuration atomically only after merged-database validation. Never fall back to stale source databases after the destination has accepted writes.

## Required tests before enabling migration

- Two independent processes: shared writer lease blocks exclusive migration until commit.
- Two independent processes: exclusive migration blocks all new writer leases.
- Writer killed while holding a lease: kernel releases the lock; no stale-lock cleanup is needed.
- Process crash during acquisition: no partial lock state leaks.
- Thread and nested-call behavior: no self-deadlock, no unintended lock upgrade, and no lease released before the outer transaction finishes.
- Lock timeout, cancellation, permission failure, symlink/reparse point, malformed lock file, missing private root, and unsupported filesystem all fail closed.
- All auth/workflow mutator tests prove the lease is held for the entire transaction.
- Constructor/schema initialization cannot write outside the lease.
- Migration refuses if any registered writer surface is missing, any worker remains active, or an external effect is unresolved.
- Linux CI is required for the first production pilot; Windows/macOS tests validate their respective lock implementations before those clients can mutate a shared control plane.

## Current implementation boundary

No cross-process maintenance lock is implemented or wired into Yuki at this point. Existing auth and workflow stores open SQLite connections independently, and their constructors perform schema/journal initialization. This document is an implementation contract only. The existing read-only preflight remains advisory and cannot establish quiescence or eliminate filesystem time-of-check/time-of-use races.

Consequential specialist execution and database consolidation remain disabled until the lock is implemented, every writer participates, process-level tests pass, and target-side fencing independently protects external effects.
