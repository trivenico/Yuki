# Phase 105 — read-only backup set verification

## API

`core.sqlite_backup_verify.verify_sqlite_backup_set(backup_set, expected_entries)` validates a published Phase 104 set against the caller's authoritative Phase 103 inventory. It returns the parsed manifest only after every check succeeds.

The verifier is read-only. It does not rewrite manifests, repair databases, delete unexpected files, move sets, or restore snapshots.

## Validation

- Requires a nonempty, unique expected inventory with safe logical names.
- Rejects symlinked set directories, manifest files and database snapshots.
- Requires a UUID-hex set directory name and a manifest set ID matching that directory.
- Caps manifest size at 1 MiB and rejects malformed JSON and duplicate JSON object keys.
- Requires the exact version-1 manifest schema, supported per-database consistency marker and timezone-aware timestamp.
- Requires the manifest database list to match the expected inventory exactly, with no duplicates or omitted databases.
- Derives each permitted snapshot filename from the expected logical database name; it never follows a manifest-supplied path.
- Rejects unexpected directory entries, non-regular files and hard-linked files.
- Checks recorded size and SHA-256, then opens each snapshot read-only and runs SQLite `PRAGMA integrity_check`.

A successful result verifies the files against their manifest and expected logical inventory. It does not prove the backup set is globally transaction-consistent across databases; Phase 104 snapshots remain individually consistent only.

## Operational boundary

Run verification before restore, retention eligibility decisions, or transfer. Treat backup storage as untrusted input even when the directory is owned by the backup service. Keep backup roots protected and encrypted through the deployment's established storage encryption and key-management controls.

The verifier is not a restore tool and does not validate application-level schema compatibility. The next phase should restore verified snapshots into a fresh isolated workspace, run application startup/schema checks there, and never overwrite a live database.

## Tests and verification

`tests/test_sqlite_backup_verify.py` covers valid verification, digest/size tampering, unexpected files, manifest path traversal, duplicate JSON keys, inventory mismatch, symlinked snapshots and invalid set directory names. Tests are committed but have not been independently executed in this environment. Confirm a successful CI run before treating them as passing.
