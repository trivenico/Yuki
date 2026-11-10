# Phase 175 — OAuth key and vault runtime wiring

## Implementation

- Adds a trusted host factory that combines the OS credential-store key provider, AES-GCM cipher, and SQLite credential vault.
- Supplies the independent HMAC key from the OS credential store.
- Adds an explicit operator command: `python scripts/initialize_oauth_keys.py`.
- Vault construction never initializes keys. It fails closed until the operator has initialized the native OS credential store.
- Tests exercise persistence and decryption across a simulated process restart using a fake credential backend.

## Operator workflow

1. Install project requirements, including the native keyring dependency.
2. Run `python scripts/initialize_oauth_keys.py` in the same OS account that will run Yuki.
3. Ensure the platform credential store is unlocked and available to the service account.
4. Construct the vault with `build_oauth_credential_vault(database_path)`.
5. Back up the OS credential store and the encrypted SQLite database using coordinated recovery procedures.

Do not run the bootstrap command under a different Windows user, macOS login, or Linux desktop session than the Yuki runtime. The OS store is account/session scoped.

## Security and integration boundaries

- The bootstrap command is explicit and does not print key material.
- No keys are written to SQLite, files, environment variables, prompts, or logs.
- The factory is not yet enabled by `scripts/run_web.py`; there is no live OAuth account-linking route.
- No multi-process rotation lock, key re-encryption job, token refresh worker, provider revocation, or MCP account wiring is enabled.
- The in-memory test backend is not a production key store.
