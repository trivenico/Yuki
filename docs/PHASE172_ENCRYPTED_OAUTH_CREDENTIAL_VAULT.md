# Phase 172 — Encrypted OAuth credential vault

## Purpose

Provide durable owner/provider/account-scoped storage for OAuth credentials without putting plaintext tokens in SQLite, configuration files, logs, skill arguments, or model context.

## Security contract

- Encryption is delegated to a trusted host-provided authenticated-encryption implementation through the `CredentialCipher` protocol. The vault does not implement cryptography and has no plaintext fallback.
- The cipher returns a key identifier and ciphertext. Decryption receives that key identifier, allowing a host key broker to support key rotation and retain old decrypt-only keys while data is re-encrypted.
- Owner, provider, account, and token kind are supplied as AEAD associated data. Copying ciphertext to another owner, provider, account, or token column must fail authentication.
- A separate host-managed HMAC key fingerprints refresh tokens for compare-and-swap. It must be stable across restarts and distinct from encryption keys.
- Access and refresh token fields are excluded from the credential dataclass representation. The account-list projection never returns tokens.
- Refresh rotation is serialized with SQLite `BEGIN IMMEDIATE` and checks both the expected credential version and current refresh-token fingerprint. Only one concurrent refresh can advance a generation.
- Disconnect deletes the encrypted credential record. External provider revocation remains a separate integration responsibility.

## API

- `store(...)`: create a new credential record; duplicate owner/provider/account records are rejected.
- `get(...)`: retrieve and decrypt one exact owner/provider/account record. The caller must already have authenticated the owner and keep the returned secret inside trusted host code.
- `rotate(...)`: compare-and-swap refresh rotation with a new access token, optional new refresh token, expiry and narrowed/current scopes.
- `list_accounts(...)`: metadata-only projection.
- `disconnect(...)`: idempotent local deletion.

## Deliberate non-goals / blockers

This phase does **not** supply a production cipher, key broker, key generation, key wrapping, key rotation job, OS keyring/KMS integration, backup key recovery, OAuth callback handler, token exchange, issuer/audience validation, automatic refresh scheduler, provider revocation, or MCP wiring. A deployment must inject a reviewed AEAD implementation and a separately managed HMAC key. No production OAuth connection should be enabled until key custody, rotation, backup/recovery, and runtime integration have been reviewed and tested.

The test cipher in `tests/test_oauth_credential_vault.py` is an authenticated test double only; it is not suitable for production.

## Validation

CI covers encrypted persistence, restart recovery, owner/account isolation, ciphertext substitution/tampering, duplicate prevention, expiry/scope validation, atomic refresh generation changes, stale refresh rejection, and disconnect behavior.
