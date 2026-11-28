# OAuth transaction metadata integrity audit

## Finding

**Severity: Medium — durable OAuth transaction context and expiry are not cryptographically bound to the transaction state or encrypted secrets.**

`SQLiteOAuthTransactionStore` stores keyed HMAC digests for state, owner, and browser session, and delegates PKCE verifier/OIDC nonce encryption to the host. However, the persisted `redirect_uri`, `issuer`, `resource`, `created_at`, `expires_at`, and `consumed_at` columns are not authenticated by this class. The ciphertext is also encrypted without a transaction-specific associated-data contract.

Consequently, an actor able to modify the SQLite database can alter expiry/consumption metadata or substitute ciphertext between transaction rows without needing the HMAC key or decrypting the secrets. This is an integrity gap independent of the existing filesystem-permission finding: filesystem hardening reduces who can reach the database, while authenticated transaction records detect unauthorized modification when that boundary is crossed or storage is restored/tampered with.

## Source evidence

- `core/oauth_transactions.py`, `create()`:
  - Computes HMAC digests only for state, owner ID, and session ID.
  - Calls the injected `encrypt` callback with only the verifier or nonce bytes; no transaction identity/context is passed as associated data.
  - Persists redirect URI, issuer, resource, and expiry as ordinary SQLite values.
- `core/oauth_transactions.py`, `consume()`:
  - Looks up the row by state digest and checks owner/session digests and the stored expiry.
  - Atomically marks the row consumed, commits, and only then decrypts verifier/nonce ciphertext.
  - Returns the unauthenticated redirect URI, issuer, and resource fields.
- `core/oauth_authorization_service.py`, `complete()`:
  - Compares returned redirect URI, issuer, and resource against the service's trusted configuration before token exchange, which prevents altered context values from selecting a different endpoint/resource in this coordinator.
  - The store's expiry check still relies on the unauthenticated `expires_at` value. Altering it can extend a transaction beyond its configured 600-second lifetime.
  - Ciphertext substitution or corruption is detected only if the injected cipher authenticates its ciphertext; it is not bound to this transaction's state/owner/session context.
- `docs/PHASE169_DURABLE_OAUTH_TRANSACTION_STORE.md` requires authenticated encryption for secret contents but does not define authenticated binding of transaction metadata.

## Impact and boundaries

- A database writer can extend a pending transaction's validity by changing `expires_at`, weakening the intended short-lived authorization/CSRF window.
- A database writer can change `consumed_at` or swap/corrupt ciphertext. The former can make the store accept a callback state again, although the OAuth authorization code itself is normally single-use and the provider should reject a repeated exchange. Ciphertext changes generally cause a fail-closed decryption/exchange failure when AEAD is used.
- The authorization coordinator independently checks transaction redirect URI, issuer, and resource against trusted service configuration before exchanging a code; this limits the impact of tampering with those context columns.
- Owner/session digests remain HMAC-protected and are still checked. This finding does not demonstrate an owner/session-binding bypass or token disclosure.
- Exploitability requires write access to the transaction database or a tampering restore path. No live deployment or filesystem was inspected.

## Recommended remediation

1. Define a versioned transaction-record authentication format covering state digest, owner/session digests, verifier ciphertext, nonce ciphertext, redirect URI, issuer, resource, creation/expiry times, and relevant lifecycle state.
2. Prefer a host cipher interface that accepts stable transaction-specific associated data (for example, a domain-separated encoding of state/owner/session digests and immutable context); do not silently change the existing callback contract without a versioned migration.
3. Authenticate mutable lifecycle metadata such as expiry and consumption state with a keyed record MAC, or redesign state transitions so authenticated immutable data is separated from trusted atomic lifecycle state.
4. Validate decrypted verifier/nonce format and transaction metadata after authentication, and fail closed on unknown record versions.
5. Add tamper tests for every authenticated field, cross-row ciphertext substitution, expiry extension, consumed-state reset, and legacy-record migration.
6. Keep the existing private-path/permission controls; record authentication complements rather than replaces filesystem protection.

## Existing controls and limitations

- State, owner, and session identifiers are stored as keyed HMAC digests.
- Callback validation and the authorization coordinator compare redirect URI, issuer, and resource against trusted configuration before token exchange.
- Consumption is serialized with `BEGIN IMMEDIATE`; decryption failure consumes the transaction and fails closed.
- The separate `docs/OAUTH_TRANSACTION_STORE_FILE_PERMISSION_AUDIT.md` already covers the lack of private-path enforcement. This report addresses record integrity and bounded-expiry enforcement, not filesystem permissions.
- No runtime tests, live database tamper exercise, or production deployment inspection was performed during this source review.
