# Phase 174 — OS-backed OAuth key provider

## Implementation

- Adds an explicit OS credential-store provider for Windows Credential Locker, macOS Keychain, and Linux Secret Service.
- Selects the native backend directly; it does not call the keyring package's automatic backend selector.
- Requires an explicit one-time `initialize()`; construction never creates keys.
- Stores AES-GCM key material, a separate HMAC key, active-key metadata, and a historical-key index in the OS credential store, outside the application SQLite database.
- Rotation writes the new key and historical index before publishing the active-key pointer. Old keys remain available for decryption.
- Missing active keys, missing historical keys, interrupted initialization, malformed key material, and backend failures fail closed with sanitized errors.
- No file, environment-variable, in-memory, or plaintext production fallback is present.

## Host integration

Install the pinned `keyring` dependency. The provider explicitly instantiates:
- Windows: `WinVaultKeyring`
- macOS: `macOS.Keyring`
- Linux: `SecretService.Keyring`

The selected backend must report positive availability. Linux requires a working Secret Service session; headless hosts without a Secret Service are unsupported and must use a separately reviewed managed KMS/HSM adapter. The provider intentionally does not fall back to a weaker backend.

The trusted host must:
1. Construct the provider and call `initialize()` once during an explicit setup/migration operation.
2. Supply `provider.current_key`, `provider.get_key`, and `provider.hmac_key()` to the AES-GCM cipher and credential vault.
3. Serialize initialization and rotation across Yuki processes. Native keyring APIs do not provide a portable transactional compare-and-swap primitive.
4. Back up/recover the OS credential store using the platform's supported mechanisms.
5. Keep historical keys until every ciphertext and retained backup encrypted by them has been re-encrypted or expired.
6. Audit rotation and recovery without logging key material.

## Important limitations

- This phase provides the native key-store adapter and key lifecycle; it does not automatically initialize keys at application startup.
- No multi-process distributed lock, cross-device key replication, automated re-encryption migration, HMAC-key rotation, or key deletion is provided.
- OS account compromise or keychain/credential-vault compromise remains a host security incident.
- Unit tests use an in-memory fake backend and do not claim to validate a real OS keychain.
- OAuth callback handling, token exchange integration, refresh scheduling, provider revocation, MCP account linking, and Upwork connection remain separate phases.
