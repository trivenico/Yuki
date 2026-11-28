# OAuth Vault Backup and HMAC-Key Continuity Audit

## Scope

Source review at PR #1346 head `0938dcabed9148d38f36d4dcfbe41ad4416ead1e`:

- `core/oauth_credential_vault.py`
- `core/oauth_os_key_provider.py`
- `core/oauth_runtime.py`
- `scripts/daily_backup.sh`
- `scripts/daily_backup.bat`
- `docs/DAILY_BACKUP.md`
- `docs/PHASE174_OS_BACKED_OAUTH_KEY_PROVIDER.md`
- `docs/PHASE175_OAUTH_KEY_VAULT_RUNTIME_WIRING.md`

Source-level review only. No backup, restore, or runtime tests were executed.

## Finding: vault restore has no HMAC-key generation binding or restore validation

**Severity: Medium — recovery integrity / OAuth refresh availability.**

The OAuth vault stores an HMAC-SHA256 digest of each refresh token in `refresh_digest`. The digest is calculated using a separate host-managed HMAC key. The key itself is stored in the OS credential store, separately from the SQLite vault.

The database schema stores AES key IDs for access and refresh ciphertext, but it stores no HMAC key identifier, version, or generation. The vault constructor accepts an arbitrary stable HMAC key and has no metadata check that it is the same key used when the rows were written.

If an operator restores an older SQLite vault snapshot but restores a newer/different OS key-store state (or otherwise loses and reinitializes the HMAC key), the access and refresh ciphertext may still decrypt when the required historical AES keys are available. However, the stored refresh-token digests were calculated with the old HMAC key. On the next refresh, `rotate()` computes the expected digest with the newly supplied HMAC key and rejects the generation as `stale_refresh_generation`. This blocks refresh for affected credentials even though the encrypted tokens themselves can be read.

The failure is not distinguishable from a genuinely stale refresh attempt at the vault API boundary. There is no startup/restore-time consistency check or durable HMAC-key generation marker.

This is a recoverability and availability defect, not evidence of token disclosure or cryptographic compromise.

## Backup workflow interaction

The repository's generic daily backup scripts archive the configured local Yuki data directory. They do not back up the OS credential store and explicitly disclaim application-specific database consistency. The OAuth runtime documentation separately instructs operators to back up/recover the OS credential store together with the encrypted SQLite database.

The vault factory accepts a caller-supplied database path and is not wired into the web runtime in the reviewed phase documentation. Therefore this review does not claim that the current daily backup necessarily includes the OAuth vault database. It identifies the missing recovery contract for any host that deploys the vault.

## Evidence

- `core/oauth_credential_vault.py`: `refresh_digest` is stored per credential row, but no HMAC key ID/version is stored in the schema.
- `SQLiteOAuthCredentialVault.__init__()`: accepts any HMAC key of at least 32 bytes; it cannot verify continuity with the key used for existing rows.
- `_refresh_digest()`: computes HMAC-SHA256 using the currently injected `self._hmac_key`.
- `rotate()`: compares the stored digest with a digest calculated from the supplied expected refresh token using the currently injected HMAC key; mismatch raises `stale_refresh_generation`.
- `core/oauth_os_key_provider.py`: stores a single stable HMAC key under `hmac.refresh`; no HMAC key rotation/versioning protocol exists.
- `scripts/daily_backup.sh` and `scripts/daily_backup.bat`: archive only the configured data directory; they do not export or snapshot the OS credential store.
- `docs/DAILY_BACKUP.md`: explicitly says backups are not application-aware/transactionally consistent.
- `docs/PHASE174_OS_BACKED_OAUTH_KEY_PROVIDER.md` and `docs/PHASE175_OAUTH_KEY_VAULT_RUNTIME_WIRING.md`: require coordinated key-store and database backup/recovery, but do not specify a machine-verifiable generation binding or restore validation.

## Recommended remediation

1. Add an HMAC key identifier/generation to vault metadata and each digest record (or a versioned digest envelope). Do not store HMAC key material in SQLite.
2. On vault construction, verify that the injected HMAC key's identifier matches the identifier used by the vault. Fail with a distinct sanitized recovery-integrity error before serving refresh operations.
3. Define a supported HMAC-key rotation migration. Recompute each refresh digest only after decrypting the corresponding refresh token, use a transaction/checkpoint strategy, and retain a recoverable old-key path until migration and backup expiry complete.
4. Introduce a coordinated backup manifest containing the vault database snapshot identity, AES key inventory/generations, HMAC key identifier, creation time, and application version. The manifest must contain no key material.
5. Provide a restore command/runbook that restores database and key-store assets as one recovery set, validates historical AES key availability and HMAC-key identity, and performs a non-destructive credential decrypt/digest self-check before enabling OAuth refresh.
6. Use SQLite's online backup API or a controlled shutdown/checkpoint procedure for the vault database. Do not copy only the main SQLite file while WAL writes may be outstanding.
7. Add integration tests for: restoring DB plus matching key store; DB plus mismatched HMAC key; DB plus missing historical AES key; and interrupted restore/rotation. Verify failures are explicit and do not overwrite recoverable key material.

## Boundaries

- Existing documentation already requires coordinated OS-key-store and database backup. This report does not claim the documented operator responsibility is absent.
- No claim is made that the generic daily backup script is currently backing up the OAuth vault database; the runtime factory takes an explicit path and is not wired into the reviewed web runtime.
- No live OS keychain backup/restore was performed.
- No runtime tests were executed.
