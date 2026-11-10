# Phase 173 — AES-GCM credential cipher adapter

## Implementation

- Adds an AES-GCM implementation of the credential vault's `CredentialCipher` contract using the maintained `cryptography` package.
- Uses a fresh 96-bit nonce for each encryption and authenticates owner/provider/account/token-kind context through AEAD associated data.
- Persists a key identifier alongside ciphertext so decryption can resolve historical keys after rotation.
- Rejects malformed key identifiers, invalid AES key lengths, malformed ciphertext and unbounded inputs. Authentication/key lookup failures are surfaced as stable generic errors.
- The cipher has no embedded key, environment-variable fallback, filesystem key store, or plaintext fallback.

## Key custody contract

The injected `OAuthKeyProvider` must be backed by an OS credential store or managed KMS/HSM in production. It must:
1. Generate and store AES keys securely outside the application database.
2. Return the active key and resolve historical key IDs.
3. Keep retired keys decrypt-only until all ciphertext has been re-encrypted and backups have expired.
4. Deny access when the key store is unavailable; never create an ephemeral replacement key.
5. Keep key bytes out of logs, model prompts, task payloads, and application configuration.
6. Support independent backup/recovery and audited rotation procedures.

The included rotating provider in tests is an in-memory test double only. No production key provider is supplied by this phase. The separate HMAC key required by the vault also remains host-managed and must be stored independently from AES keys.

## Validation

CI exercises randomized nonces, round trips, tamper detection, associated-data binding, historical-key decryption after rotation, missing-key failure, key-length validation, and input bounds.

## Remaining production gates

No OS keyring/KMS implementation, re-encryption migration, key recovery process, vault runtime wiring, OAuth callback/token exchange, refresh scheduler, provider revocation or MCP connection is enabled. Do not connect earning platforms until those controls and the full OAuth chain are integrated and reviewed.
