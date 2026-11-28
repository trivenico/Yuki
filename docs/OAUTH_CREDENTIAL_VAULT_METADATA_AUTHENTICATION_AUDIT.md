# OAuth Credential Vault Metadata Authentication Audit

## Scope

Reviewed at PR #1346 head `9373cf2488e7b1dc71d292f2e2f89af11aadbadc`:

- `core/oauth_credential_vault.py`
- `core/oauth_aead.py`
- `core/oauth_os_key_provider.py`
- `core/oauth_runtime.py`
- `tests/test_oauth_credential_vault.py`
- Existing vault and key-provider phase/audit documentation.

Source-level review only. No runtime tests, production keychain operations, or database-tampering experiments were executed.

## Finding: security-relevant vault metadata is outside the AEAD authentication boundary

**Severity: Medium — credential authorization integrity.**

The vault encrypts access and refresh token values with authenticated encryption. Its associated data is currently only the tuple `[1, owner_id, provider_id, account_id, token_kind]`. Other security-relevant fields are stored as ordinary SQLite columns and are not cryptographically bound to either encrypted token:

- `scopes_json`
- `expires_at`
- `version`
- `connected_at`
- `updated_at`
- the refresh-token digest and its interpretation/generation

The schema has structural constraints for some values, and `get()` parses and validates the scope syntax. Those checks do not establish that the values are the values originally authorized and persisted. A party able to alter the SQLite database without access to the OS-held AEAD/HMAC keys can modify these fields while leaving both token ciphertexts untouched.

For example, changing `scopes_json` to a syntactically valid broader scope set causes `get()` and `list_accounts()` to return that broader set: token decryption still succeeds because scopes are absent from AAD. If a host component uses the vault's returned scope metadata to decide which operations are authorized, this can produce a local authorization decision inconsistent with the grant actually issued by the provider. Changing `expires_at` can similarly make a locally expired token appear valid for longer. Altering `version` or `refresh_digest` can disrupt refresh compare-and-swap behavior or cause denial of service.

This is not a claim that the provider will honor a token beyond its own server-side grant, nor that an attacker with only read access can perform the modification. It is an integrity gap in the vault's at-rest tamper-detection contract: AEAD currently protects token bytes and account/token placement, but not the metadata that describes and governs those credentials.

## Evidence

- `SQLiteOAuthCredentialVault._aad()` includes only schema marker, owner, provider, account, and token kind.
- `store()` and `rotate()` persist scopes, expiry, version, and timestamps separately from ciphertext and without a metadata authenticator.
- `get()` decrypts tokens using AAD that does not include the selected metadata columns, then independently parses `scopes_json`.
- `list_accounts()` returns parsed scopes and expiry without decrypting a token or verifying any metadata authenticator.
- `tests/test_oauth_credential_vault.py` covers ciphertext tampering and cross-account ciphertext substitution, but has no test that modifies valid-looking scope/expiry/version metadata and expects an integrity failure.
- `docs/PHASE172_ENCRYPTED_OAUTH_CREDENTIAL_VAULT.md` describes owner/provider/account/token-kind binding and encrypted credential storage, but does not specify an authenticated metadata envelope.

## Recommended remediation

1. Define a canonical, versioned metadata representation for each credential generation. Include owner/provider/account, token kind, expiry, normalized scopes, credential version, connection timestamp, and any other fields whose integrity affects authorization or lifecycle decisions.
2. Authenticate that representation with AEAD AAD for each encrypted token, or add a separately keyed metadata MAC with an explicit key identifier and rotation/recovery contract. Avoid a plain unkeyed checksum.
3. Ensure metadata-only reads such as `list_accounts()` verify authenticity before returning scopes or expiry. If verification requires decrypting a token, make that cost and behavior explicit rather than returning unauthenticated security claims.
4. Bind the refresh digest and generation semantics to the authenticated metadata envelope; ensure changes to version or refresh state cannot silently alter the meaning of a valid credential row.
5. Add regression tests that mutate scopes to another syntactically valid set, expiry, version, timestamps, refresh digest, and owner/provider/account columns. Each mutation must fail closed with a sanitized integrity error. Preserve tests for legitimate store, rotate, restart, and disconnect behavior.
6. Version the envelope and provide a deliberate migration path for existing rows. Do not silently treat legacy rows as fully authenticated or rewrite them without a recoverable backup/rollback procedure.

## Related lifecycle limitations (already documented; not new findings)

- The OS key provider retains historical AES keys and has no automated vault re-encryption or key-retirement workflow.
- Key-provider initialization/rotation serialization is process-local; the host documentation requires cross-process serialization by the owner.
- HMAC-key continuity across database restore is already covered by `docs/OAUTH_VAULT_BACKUP_HMAC_CONTINUITY_AUDIT.md`.
- The AES adapter's acceptance of 16- and 24-byte keys despite its AES-256 name is already covered by `docs/OAUTH_AES256_KEY_STRENGTH_POLICY_AUDIT.md`.

These items were not duplicated as new findings.

## Boundaries

- No token or key material was accessed.
- No production OS credential store was modified.
- No live OAuth provider or token-refresh flow was exercised.
- No runtime tests were executed; the finding is based on the implementation's AAD construction and metadata read/write paths.
