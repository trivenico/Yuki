# Phase 108 — explicitly approved SQLite backup pruning

## API

`core.sqlite_backup_retention.prune_sqlite_backup_sets(..., approved_set_ids=(...), explicit_approval=True)` is the destructive counterpart to the Phase 107 dry-run planner.

The caller must obtain operator approval for the exact set IDs and pass those IDs explicitly. The API rejects absent approval, empty or malformed IDs, duplicates, and any selected set that is not currently eligible. It returns the IDs removed.

## Guardrails

- Rebuilds the full retention plan before deletion and again immediately before each selected set.
- Verifies every discovered backup set against the current explicit inventory; any corrupt or unexpected set aborts the operation.
- Protects the newest `keep_last` verified sets and all pinned IDs.
- Uses POSIX directory file descriptors, no-follow directory opens, inode/device identity checks, a same-root quarantine rename, exact inventory checks, and single-link regular-file checks before unlinking.
- Deletes only the exact manifest and database snapshot filenames derived from the expected inventory.
- Fails closed on platforms without the required `dir_fd` operations; there is no path-based fallback.
- No automatic scheduler, background task, retention policy mutation, or implicit approval is included.

## Recovery boundary

The backup root must be private and writable only by the trusted backup service. The implementation narrows path races using directory descriptors and identity checks, but it is not a defense against a malicious process running as the same service account with concurrent write access. Keep the backup service account isolated and do not share the backup root with untrusted processes.

If an operating-system error occurs after quarantine or during unlink, the operation can leave a hidden `.yuki-prune-<id>` directory containing a partially removed candidate. Do not blindly rename or delete it. Stop pruning and investigate against the retained verified sets and filesystem state. The operation never targets a retained set, but partial failure is not transactionally reversible.

Approval is an application-level caller contract, not cryptographic proof of an authenticated human. The API does not implement an approval workflow or audit log; production integration must enforce authenticated operator authorization and durable audit before invoking it.

## Tests and verification

`tests/test_sqlite_backup_retention.py` now covers explicit approval, pruning one approved eligible set, preserving the newest set, refusing to prune the only known-good set, and refusing to prune pinned sets, in addition to Phase 107 planner coverage.

Tests are committed but have not been independently executed in this environment. Confirm CI on the latest PR head before treating them as passing.
