# OAuth connection registry database permissions audit

## Finding

**Severity: Medium — OAuth connection metadata is not protected by the private-SQLite path preparation used for the credential vault.**

`SQLiteOAuthConnectionRegistry` creates its parent directory with `Path.mkdir(parents=True, exist_ok=True)` and opens its database through ordinary `sqlite3.connect()`. It does not call `prepare_private_sqlite_path()`, which is the repository's explicit helper for owner-only SQLite files.

On POSIX systems, a newly created directory therefore inherits the process umask rather than being required to be private, and the SQLite database file is created with the SQLite/OS default permissions subject to that umask. With a common umask such as 022, the database can be readable by other local users when its parent directory is traversable. Existing registry files and parent directories are not checked for owner, mode, symlink, hard-link, or regular-file constraints.

The registry intentionally excludes bearer and refresh tokens, but it stores owner IDs, provider IDs, provider account IDs, granted scopes, and connection timestamps. These are sensitive integration metadata and can reveal user/provider relationships and granted capabilities. The separate credential vault already uses `prepare_private_sqlite_path()` and enforces a private POSIX parent and mode-0600 database file.

## Source evidence

- `core/oauth_connection_registry.py`, `SQLiteOAuthConnectionRegistry.__init__()`:
  - Creates the parent with `Path(...).parent.mkdir(parents=True, exist_ok=True)`, without a restrictive mode or post-create validation.
  - Calls `sqlite3.connect(self.database_path, timeout=5.0)` through `_connect()`.
  - Does not validate the existing database file or parent directory.
- `core/oauth_connection_registry.py`, registry schema:
  - Persists `owner_id`, `provider_id`, `account_id`, `scopes_json`, and `connected_at`.
- `core/oauth_credential_vault.py`, `SQLiteOAuthCredentialVault.__init__()`:
  - Calls `prepare_private_sqlite_path(database_path)` before opening the credential database.
- `core/sqlite_file_security.py`, `prepare_private_sqlite_path()`:
  - Creates/validates a private parent directory on POSIX.
  - Rejects unsafe ownership, non-private parent modes, symlinks where supported, non-regular files, and hard-linked database files.
  - Sets the database file mode to 0600.
- `tests/test_oauth_connection_registry.py`:
  - Tests parent creation and CRUD/owner isolation.
  - Does not assert directory/database permissions or rejection of unsafe pre-existing paths.

## Impact and boundaries

- Other local users may be able to read OAuth connection metadata if the configured registry path is in a traversable shared directory and process umask permits it.
- The metadata can disclose which users have connected which providers/accounts and which scopes were granted.
- This finding does **not** establish exposure of access tokens, refresh tokens, or credential ciphertext; those reside in the separate encrypted vault.
- Exploitability depends on deployment OS, umask, configured path, and surrounding directory permissions. No live host filesystem was inspected, so actual exposure is not asserted.

## Recommended remediation

1. Apply `prepare_private_sqlite_path()` to the registry database path, matching the credential vault's file-security boundary.
2. If some deployments intentionally place the registry in a shared directory, define and validate an explicit supported policy rather than silently accepting ambient permissions.
3. Ensure WAL/SHM sidecar files are created under the same private parent directory. Do not place the registry in a broadly traversable directory.
4. Add POSIX tests asserting a newly created registry directory is owner-only and the database file is mode 0600.
5. Add tests that reject an existing permissive parent, wrong-owner path, symlink, non-regular file, and hard-linked database where supported.
6. Keep platform-specific behavior explicit: the helper documents that Windows ACLs are managed by the user profile/deployment installer, so add Windows deployment guidance and tests for the intended ACL boundary.
7. Review existing deployments before tightening behavior, and provide a clear migration/error message for legacy registry paths that fail the private-path policy.

## Limitations

This is a source-level filesystem-permission finding. No host filesystem permissions were inspected, no local runtime tests were run, and no data exposure was observed. The impact is conditional on deployment path and OS permission configuration.
