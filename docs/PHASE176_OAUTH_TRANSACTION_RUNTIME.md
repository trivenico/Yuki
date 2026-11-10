# Phase 176 — OAuth transaction runtime integration

## Implementation

- Adds a trusted runtime factory connecting durable OAuth transactions to the native OS key provider and AES-GCM cipher.
- PKCE verifier ciphertext is stored in a versioned envelope containing only the key identifier and authenticated ciphertext.
- AEAD associated data binds the ciphertext to the HMAC digests of OAuth state, owner, and browser session. Copying ciphertext between transactions fails authentication.
- The independent HMAC key remains in the OS credential store and is used for state/owner/session digests.
- The factory never initializes keys implicitly; it fails closed until explicit operator bootstrap.
- The transaction store keeps its existing one-time, owner/session-bound atomic consume behavior and supports existing legacy injected callbacks for compatibility.

## Security behavior

- The transaction state itself is not stored in plaintext; only its HMAC digest is stored.
- PKCE verifier is encrypted with AES-GCM and authenticated context.
- Wrong owner/session cannot consume a transaction.
- Successful consumption is atomic and one-time. A decryption failure after consumption burns the transaction rather than permitting replay.
- Expired transactions are not usable.
- Key IDs permit decryption after encryption-key rotation, provided historical keys remain available.

## Boundaries

- This phase does not implement an HTTP callback endpoint, browser session authentication, OAuth token exchange, issuer-response validation, refresh scheduling, provider revocation, or MCP account linking.
- The existing transaction store schema does not encrypt redirect URI, issuer, or resource metadata; those values are not bearer credentials, but should be treated as sensitive operational metadata.
- OS keychain backup/recovery and multi-process rotation serialization remain operator responsibilities.
- Tests use a fake OS credential backend and the real AES-GCM primitive; they do not prove platform keychain behavior.
