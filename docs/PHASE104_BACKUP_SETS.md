# Phase 104 — verified multi-database SQLite backup sets

## API

`core.sqlite_backup_set.create_sqlite_backup_set(entries, backup_root, workspace_root=...)` accepts the explicit inventory returned by Phase 103 and publishes one directory per successful backup set.

The backup root must already exist, be outside the live workspace, and on POSIX be owned by the current service user with no group/other permissions. Provision it on a protected volume; do not place it inside the Yuki workspace.

For each database, the coordinator:
1. Uses the SQLite online backup helper (including committed WAL state).
2. Runs an independent read-only integrity check.
3. Records the database key, generated filename, byte size, SHA-256 digest and integrity result.
4. Writes and fsyncs a versioned JSON manifest in a private staging directory.
5. Atomically renames the staging directory to a unique backup-set ID.

Only the final directory contains `manifest.json` and is treated as a published set. If a database backup or verification fails before publication, the coordinator removes its private staging directory and leaves prior backup sets untouched. It never overwrites an existing set.

## Manifest semantics

Manifest format version 1 records:
- unique backup-set ID and UTC creation timestamp;
- `snapshot_consistency=per_database`;
- each database's logical inventory key, generated snapshot filename, size, SHA-256 digest and SQLite integrity result.

The manifest deliberately omits absolute source paths to avoid leaking deployment filesystem layout. Consumers must validate filenames against the expected inventory rather than trusting arbitrary manifest paths.

## Consistency boundary

Every individual SQLite file is transactionally consistent, including committed WAL data. The set is not a cross-database atomic snapshot: writes may occur between snapshots. If application invariants span multiple databases, the operator must quiesce writes or use a future application-wide maintenance barrier while creating the set. Do not interpret the timestamp as a globally consistent database transaction point.

The coordinator does not encrypt, upload, schedule, retain, prune, or restore backups. It does not provision the backup volume or keys. Use established volume/provider encryption and separately managed recovery keys; do not invent a custom cryptographic format.

## Tests and verification

`tests/test_sqlite_backup_set.py` covers complete manifest publication, per-file integrity and content, partial failure cleanup, preservation of an existing set, rejection of backup roots inside the workspace, and path-safe database names. Tests are committed but have not been independently executed in this environment. A successful GitHub Actions run is required before treating them as passing.
