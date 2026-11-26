# Maintenance fence implementation status

**Review date:** 2026-11-26
**Branch:** research/reference-projects-integration-assessment

## Implemented prototype

The branch now contains:

- `core/maintenance_fence.py`
- `tests/test_maintenance_fence.py`

The prototype provides a context-managed exclusive lease. It serializes participating threads with a per-path reentrant lock and participating processes with an OS lock on a persistent file beneath the validated private profile directory. POSIX uses nonblocking `flock(LOCK_EX)`; Windows uses a byte-range `msvcrt.locking` lock. Nested calls on the same thread/path reuse the outer OS lease. Lock timeout, unsafe file type, hard links, unexpected POSIX ownership/permissions, and unsupported platforms fail closed. Timeout validation rejects booleans, negative/out-of-range values, NaN, infinities, and arbitrarily large integers without leaking conversion exceptions. The lock file is retained after release; the kernel releases the lock when the descriptor closes or the process exits.

The implementation deliberately uses exclusive locking for all operations. It does not yet provide shared-reader concurrency, a lock upgrade path, owner metadata, or a distributed/network-filesystem lock guarantee. On platforms with `os.register_at_fork`, a child process closes inherited lock descriptors and resets inherited thread coordination state; a regression test covers acquiring the lock after the parent releases it. Timeout boundary and invalid-value tests are also included. Lock-file validation failures close the opened descriptor, including when the path disappears or changes during inode verification.

## Important limitations

- Fork handling is best-effort process hygiene, not a guarantee for arbitrary native extensions or unsupported process-creation mechanisms. Prefer spawn/exec for worker processes; do not fork a multithreaded Yuki process while database work is active.
- `BuiltInAuthService` and `WorkflowRunStore` now accept an optional injected `maintenance_fence` and acquire its lease before opening each SQLite connection. The lease remains held through schema initialization, journal setup, transaction commit/rollback, and connection close. Focused tests assert the injected lease is entered for initialization and database use.
- This is **opt-in participation only**. Existing production compositions do not yet inject a shared fence, and callers can still construct either store without one. The fence therefore does not yet establish application-wide quiescence or prevent bypass by CLI, desktop, scheduler, recovery, or worker paths.
- The two stores still use separate SQLite databases. A shared maintenance lock coordinates migration/quiescence only when every writer participates; it does not make auth revocation and workflow claim one atomic transaction.
- The private-profile validator currently fails closed on Windows, so the Windows lock adapter is not usable in production until a verified Windows DACL/private-profile adapter exists.
- POSIX advisory locking semantics depend on the underlying filesystem. Production deployment must use a supported local filesystem; network filesystems and container volume semantics require explicit verification.
- A lock cannot stop an already-running provider call or undo an external side effect. Worker draining and target-side authorization/fencing remain separate requirements.
- The implementation has not been executed in local tests or CI at the time of this status update.

## Next integration gates

1. Add a single canonical control-plane composition root that constructs one fence and injects it into both stores; make production composition require it rather than silently defaulting to no fence.
2. Define and enforce the canonical profile-root policy, and reject stores wired to incompatible lock authorities.
3. Audit all auth and workflow mutators, including nested helpers, event writes, retries, revocation and recovery; verify no direct SQLite connection bypasses `_connect()`.
4. Wire desktop, CLI, scheduler, recovery and worker composition to the same authority; keep provider/network work outside the lease.
5. Add multiprocess tests for revocation-vs-claim, concurrent claims, process death, timeout, and migration exclusivity.
6. Implement Windows private-profile ACL validation before claiming Windows support.
7. Only then build an exclusive migration operation that rechecks inventories under the lease and uses SQLite's backup API.

The prototype is not migration authorization. Database consolidation and consequential specialist execution remain disabled until all writer paths participate and the unified auth/workflow transaction boundary is independently verified.
