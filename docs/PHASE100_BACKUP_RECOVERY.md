# Phase 100 — SQLite backups and workforce recovery

## Consistent SQLite snapshots

`core.sqlite_backup.backup_sqlite_database(source, destination)` creates a transactionally consistent SQLite snapshot using the SQLite online backup API. This is safe for databases using WAL mode; it includes committed data visible to the source connection without requiring a raw copy of the main database file and sidecars.

The helper:
- Opens the source read-only and rejects symlinks, non-regular files, and hard-linked source files.
- Requires an existing destination directory and rejects destination symlinks or unsafe existing destination files.
- Writes to a temporary file in the destination directory.
- Runs `PRAGMA integrity_check` against the completed snapshot.
- Applies owner-only permissions on POSIX, fsyncs the snapshot, and atomically replaces the destination only after verification.
- Leaves an existing destination untouched if snapshot creation or integrity verification fails.

Example:

```python
from core.sqlite_backup import backup_sqlite_database

backup_sqlite_database(
    "/workspace/auth.sqlite3",
    "/workspace-backups/auth-2026-11-19.sqlite3",
)
```

The destination directory must be provisioned with restrictive permissions by the operator. Store backups outside the live workspace and preferably on a separate protected volume. Protect backups as sensitive data: they contain authentication and user records. This helper intentionally does not implement scheduling, retention, remote upload, encryption/key management, or multi-database all-or-nothing snapshots. Back up each database explicitly and maintain an operator-controlled manifest so a restore set can be identified. Do not treat a collection of individually atomic files as a single cross-database transaction.

A restore procedure must stop Yuki, restore into an isolated workspace, run application-specific validation, and only then promote the restored workspace. Never overwrite a live production database during a restore test.

## Workforce lease recovery

During ASGI lifespan startup, the runtime now invokes `workforce_application.engine.recover_expired_leases()` before reporting startup complete. This requeues expired running tasks when retry attempts remain, or marks them failed when their retry budget is exhausted, using the task engine's existing transactional lease logic.

A lease that has not yet expired is not reclaimed early: the previous worker may still be executing. Workers must continue to use lease ownership checks and heartbeats. This startup recovery does not provide graceful worker cancellation or distributed execution guarantees; SQLite task storage remains intended for a single-host deployment.

If recovery raises or returns an invalid result, startup fails closed and the readiness endpoint remains unavailable.

## Tests and verification boundary

- `tests/test_sqlite_backup.py` covers committed WAL data, integrity validation, private destination permissions on POSIX, preservation of an existing backup after failure, and symlink rejection.
- `tests/test_workforce_startup_recovery.py` covers recovery-before-startup and fail-closed behavior.
- These tests have been added but have not been independently executed here. Check GitHub Actions for the current branch head before treating them as passing.
