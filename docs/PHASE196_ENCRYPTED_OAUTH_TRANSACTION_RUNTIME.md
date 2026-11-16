# Phase 196 — Encrypted OAuth transaction runtime factory

## What changed

`build_oauth_transaction_store(...)` in `core/oauth_runtime.py` now composes the existing durable SQLite PKCE/nonce transaction store with the same explicitly initialized OS keyring and AES-GCM key provider used by the credential vault.

- PKCE verifiers and OIDC nonces are encrypted with AES-GCM before persistence.
- The database stores a non-secret key identifier alongside the ciphertext so transactions created before key rotation remain decryptable.
- The transaction store uses the separate stable HMAC key for state, owner, and session digests.
- Key initialization remains an explicit operator action. Factory construction fails closed if the OS key store is not ready.
- No raw AES or HMAC key material is written to SQLite, files, environment variables, logs, or model context.

## Host usage

After running `python scripts/initialize_oauth_keys.py` under the same OS account as Yuki:

```python
from core.oauth_runtime import build_oauth_transaction_store

transactions = build_oauth_transaction_store(
    workspace / "oauth-transactions.sqlite3",
)
```

Construct the credential vault separately with `build_oauth_credential_vault(...)`. Both factories use the same native OS key store namespace by default; deployments using a custom service namespace must pass a matching `OSKeyringOAuthKeyProvider` to both.

## Validation

Tests cover fail-closed construction before key initialization, persistence and decryption after a simulated process restart, successful decryption after AES key rotation, and one-time transaction consumption.

This adds a missing reusable storage primitive for the upcoming Google Gmail host composition. It does not enable OAuth in `scripts/run_web.py`, create Google client credentials, register callback routes, or connect a real Google account.
