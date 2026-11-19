# Phase 107 — read-only SQLite backup retention planning

## API

`core.sqlite_backup_retention.plan_sqlite_backup_retention(backup_root, expected_entries, workspace_root=..., keep_last=..., pinned_set_ids=...)` returns an immutable `SQLiteBackupRetentionPlan` containing ordered verified, retained, and eligible backup-set IDs.

This phase is deliberately a dry-run planner. It does not delete, move, rename, rewrite, or otherwise mutate backup data.

## Safety rules

- Requires a pre-provisioned backup root outside the live workspace; on POSIX it must be owned by the service user and private.
- Enumerates only direct children of that root. Every child must be a real UUID-hex backup-set directory; symlinks, files, temporary directories and unknown entries abort planning.
- Verifies every discovered set against the current explicit database inventory using the Phase 105 read-only verifier. A single invalid or corrupted set aborts the entire plan; it is never silently treated as an eligible deletion.
- Sorts sets by their timezone-aware manifest creation timestamp, newest first, with set ID as a deterministic tie-breaker.
- Requires an exact positive integer `keep_last` (booleans are rejected).
- Retains the newest `keep_last` verified sets and all explicitly pinned IDs. A pinned ID that is absent or invalid aborts planning.
- Reports all remaining verified IDs as eligible candidates only. No deletion API is provided.

## Operational boundary

The plan is advisory and is not a deletion authorization. Before any future prune implementation, it must revalidate the candidate and retained last-known-good set immediately before mutation, defend against directory-entry races, and use a separately authorized explicit operation. Automatic scheduling and unattended deletion are out of scope.

An empty backup root produces an empty plan; operators must create and verify an initial backup before relying on retention.

## Tests and verification

`tests/test_sqlite_backup_retention.py` covers newest-set retention, pinned sets, corrupted-set fail-closed behavior, unexpected root entries, boolean policy rejection, missing pins, and proof that planning leaves backup directories present and unchanged.

Tests are committed but have not been independently executed in this environment. Confirm CI for the latest PR head before treating them as passing.
