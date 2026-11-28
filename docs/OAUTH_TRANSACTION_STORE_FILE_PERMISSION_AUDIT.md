# OAuth transaction store filesystem-permission audit

## Finding

**Severity: Medium — the durable OAuth transaction database does not enforce the private-workspace boundary required by its own integration contract.**

`SQLiteOAuthTransactionStore` creates its parent directory using ordinary `Path.mkdir(parents=True, exist_ok=True)` and opens the database through ordinary `sqlite3.connect()`. It does not use the repository's `prepare_private_sqlite_path()` helper and does not validate permissions, ownership, symlink status, hard links, or regular-file type for an existing database path.

The store persists:
- encrypted PKCE code verifiers;
- encrypted OIDC nonces when OIDC is enabled;
- keyed HMAC digests for state, owner ID, and browser session ID;
- redirect URI, issuer, resource, creation time, expiry, and consumption state.

The encryption callbacks protect verifier/nonce contents when correctly implemented with authenticated encryption, and the HMACs avoid storing raw state/owner/session values. Those controls do not make a shared or permissively readable database path acceptable: the remaining metadata can disclose authorization activity and identity/provider context, and the encrypted transaction artifacts remain security-sensitive stored material.

## Source evidence

- `core/oauth_transactions.py`, `SQLiteOAuthTransactionStore.__init__()`:
  - Creates the parent with `Path(...).parent.mkdir(parents=True, exist_ok=True)`, without a restrictive mode or validation.
  - Opens the database with `sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)`.
  - Does not check an existing file or parent directory for ownership, permissions, symlinks, hard links, or regular-file type.
- `core/oauth_transactions.py`, transaction schema and `create()`:
  - Persists encrypted verifier and nonce ciphertexts and authorization context in SQLite.
  - Persists keyed digests for state, owner, and session identifiers.
- `core/sqlite_file_security.py`, `prepare_private_sqlite_path()`:
  - Provides the repository's explicit POSIX private-parent and mode-0600 database-file enforcement, plus unsafe path checks.
- `core/oauth_credential_vault.py`:
  - Uses `prepare_private_sqlite_path()` for the long-lived credential database, demonstrating the intended protection pattern.
- `docs/PHASE169_DURABLE_OAUTH_TRANSACTION_STORE.md`:
  - Explicitly states that transaction storage must live in a private workspace directory with appropriate OS permissions, backup protection, and database access controls.
- `tests/test_oauth_transactions.py` (transaction-store test suite):
  - Covers transaction lifecycle and cryptographic behavior; no filesystem permission assertions were found in the reviewed test coverage.

## Impact and boundaries

- On POSIX systems, a newly created transaction directory/database may inherit ambient umask and parent-directory permissions rather than being guaranteed owner-only.
- If the configured path is shared or traversable by other local users, they may read authorization metadata and ciphertext records, or tamper with transaction state subject to filesystem write permissions.
- The source does not demonstrate plaintext PKCE verifier or OIDC nonce persistence; those values are encrypted before storage.
- Actual exposure depends on the configured path, deployment umask, OS, and directory ACLs. No live deployment filesystem was inspected and no data exposure is asserted.

## Recommended remediation

1. Pass the transaction database path through `prepare_private_sqlite_path()` before opening it, consistent with the credential vault.
2. Ensure the parent directory is private and trusted, and ensure SQLite journal/temporary files are created inside that same protected directory.
3. Add POSIX tests asserting owner-only parent permissions and mode 0600 for a newly created database.
4. Add tests for permissive existing parents, wrong ownership, symlinked paths, non-regular files, and hard-linked database files where supported.
5. Document the corresponding Windows ACL responsibility and validate the deployment installer/user-profile boundary.
6. Provide an explicit migration/error path for existing installations whose transaction database is outside the required private workspace; do not silently weaken or relocate user data.

## Limitations

This is a source-level filesystem-boundary finding. No live host permissions were inspected, no runtime tests were run, and no production exposure was observed. Exploitability is conditional on deployment path and OS permission configuration.
