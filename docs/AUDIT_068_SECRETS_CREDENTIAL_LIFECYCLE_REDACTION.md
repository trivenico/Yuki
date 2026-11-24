# Audit 068 — Secrets Management, Credential Lifecycle & Redaction

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Audit date:** 2026-11-24  
**Scope:** OAuth credential storage, token parsing and refresh, MCP credential use, disconnect/revocation, API exposure, and secret leakage through model/tool/audit boundaries.

## Executive summary

The inspected OAuth implementation has a comparatively strong foundation: credential values are encrypted through a host-provided authenticated-encryption cipher; ciphertext is bound to owner/provider/account/token kind with associated data; refresh-token compare-and-swap uses a separate HMAC key; token objects suppress credentials in their default representation; token responses are shape/scope/lifetime validated; refresh is single-attempt and versioned; OAuth APIs return non-secret connection metadata; and approval/MCP audit records hash sensitive identifiers rather than storing bearer material.

A concrete lifecycle race was found and fixed in a separate implementation PR: disconnect previously fetched a credential, performed a potentially slow remote revocation, then deleted the account row without passing the credential version. A concurrent refresh or reconnection could therefore be deleted by a stale disconnect operation. The fix uses the vault's existing version-fenced delete.

The review did not establish a single repository-wide secret-redaction layer or full key rotation/retirement lifecycle. These remain architecture and operational verification items, not claims that every log or output currently leaks credentials.

## Reviewed source

- `core/oauth_credential_vault.py`
- `core/oauth_refresh_service.py`
- `core/oauth_disconnect_service.py`
- `core/oauth_authorization_service.py`
- `core/oauth_token_response.py`
- `core/oauth_connection_registry.py`
- `core/oauth_mcp_connection.py`
- `core/mcp_client.py`
- `api/oauth_connections.py`
- `api/oauth_authorization.py`
- `core/approval_audit.py`
- `tests/test_oauth_credential_vault.py`
- `tests/test_oauth_refresh_service.py`
- `tests/test_oauth_disconnect_service.py`
- `tests/test_oauth_mcp_connection.py`
- `tests/test_oauth_connections_api.py`
- `tests/test_oauth_authorization_api.py`

## Findings

### P1 — Disconnect did not fence deletion to the credential generation it had inspected — FIX SUBMITTED

**Evidence:** Before the fix, `OAuthDisconnectService.disconnect` called `vault.get`, used the returned access/refresh token for remote revocation, and later called `vault.disconnect` without `expected_version`. The vault already supports conditional deletion by version.

**Impact:** A concurrent refresh/reconnect could change the stored credential while the remote revocation request was in flight. The stale disconnect could then delete the newer credential even though it had only attempted remote revocation using the previous token.

**Remediation submitted:** Implementation PR #1126 passes the read credential's version to `vault.disconnect`, rejects credential records without a valid version, and raises a sanitized `credential_changed_during_disconnect` error if the version fence rejects deletion. Regression coverage exercises the stale-generation case.

### P2 — Secret redaction is not established as a shared cross-system contract

**Evidence:** The inspected modules deliberately sanitize OAuth service exceptions, suppress token fields from dataclass `repr`, keep OAuth connection API responses metadata-only, and hash actor/pending identifiers in approval audit records. MCP results are labeled untrusted and scanned for prompt-injection signals, but the scanner is not a secret detector or output-redaction facility.

**Risk:** Local safeguards do not prove that every logger, trace exporter, exception handler, provider adapter, plugin, memory writer, support bundle, or future API response redacts credentials. A structured object can also expose fields through explicit serialization even when its `repr` is safe.

**Recommendation:**
- Define a centralized secret classification and redaction contract for access tokens, refresh tokens, client secrets, session tokens, authorization codes, private keys, and provider-specific credentials.
- Apply redaction at logging/telemetry/export boundaries, not only in individual service exception messages.
- Add canary-secret tests for logs, exceptions, traces, memory, tool output, and API serialization.
- Avoid serializing credential-bearing dataclasses generically; expose explicit metadata DTOs.

### P2 — Cryptographic key custody and rotation depend on host integration and are not fully demonstrated here

**Evidence:** `SQLiteOAuthCredentialVault` requires a host-provided cipher and a separate HMAC key of at least 32 bytes. Ciphertext stores a key ID and uses associated data binding owner/provider/account/token kind. The module documents that the HMAC key must be stable across restarts and distinct from the AEAD key. The inspected contract provides encrypt/decrypt but no vault-level key inventory, rewrap/migration, retirement, or rotation workflow.

**Risk:** Safe operation depends on deployment wiring: durable key custody, distinct key material, backup/restore compatibility, key ID availability, rotation, retirement, and recovery must all be implemented and tested by the host. A database backup without compatible keys may be unrecoverable; a compromised key may require coordinated re-encryption and provider token rotation.

**Recommendation:**
- Document the production key provider and its access policy; do not store encryption/HMAC keys beside the SQLite database.
- Implement and test key version rotation/rewrapping, old-key retirement, backup restoration, and disaster recovery.
- Verify key separation and minimum entropy in deployment configuration.
- Ensure key IDs and cryptographic failures are observable without logging ciphertext or plaintext secrets.

### P2 — Credential deletion is logical database deletion, not a guaranteed physical-erasure promise

**Evidence:** The vault removes the credential row in SQLite and uses WAL mode. The implementation does not claim secure physical erasure of previous database pages, WAL frames, filesystem snapshots, or backups.

**Risk:** Local disconnection prevents normal application lookup, but previously written encrypted ciphertext may remain in database/WAL/backup media. This is expected for many database systems and must not be described as forensic erasure.

**Recommendation:**
- Keep credentials encrypted at rest with independently managed keys.
- Define retention and purge policy for database backups, WAL/checkpoint files, snapshots, and disaster-recovery copies.
- Use cryptographic erasure/key retirement where the operational model supports it.
- Clearly distinguish logical disconnect, remote provider revocation, and physical media erasure in product/API documentation.

### P2 — Credential exposure testing is focused on OAuth modules, not a demonstrated whole-application inventory

**Evidence:** The inspected OAuth tests verify encrypted persistence, owner/account isolation, token rotation, redacted representations, metadata-only connection responses, and sanitized disconnect errors. The MCP client obtains credentials lazily from a host provider and validates bearer-token syntax before sending them to the pinned transport.

**Risk:** These controls do not prove that every provider integration, plugin, agent memory path, generated support report, or observability integration avoids credential exposure. The review was scoped to the listed files.

**Recommendation:**
- Inventory every credential source and every sink (logs, traces, prompts, memory, tool results, API responses, exports, crash reports).
- Add integration-level canary tests across the complete runtime.
- Ensure only the minimum required credential is exposed to a provider transport and never to model context.
- Add explicit credential access audit events that record identity, provider/account, operation, and outcome without token values.

## Existing controls worth preserving

- Host-provided AEAD cipher; no plaintext fallback.
- Owner/provider/account/token-kind associated data prevents ciphertext swapping across contexts.
- Separate HMAC fingerprint for refresh-token generation checks.
- Credential values excluded from default dataclass representations.
- Strict token response field, scope, token type, and expiry validation.
- Single-attempt refresh behavior for ambiguous network outcomes.
- Owner-scoped credential lookup and metadata-only OAuth connection APIs.
- MCP credential lookup is lazy and bound to the configured owner/provider/account.
- Approval audit stores hashes rather than raw pending IDs/actors.
- Vault supports version-fenced disconnect; PR #1126 now uses it in the service layer.

## Verification status

- Source review: completed for the paths listed above.
- Implementation: PR #1126 submitted with source change and regression tests.
- Tests added: stale disconnect race, exact credential-version binding, missing/invalid version handling, and existing disconnect behaviors.
- Tests executed: none in this environment.
- Merge/deployment: not performed.
- Key custody, production key rotation, complete redaction coverage, and backup retention require deployment-level verification.

## Follow-up

Next recommended audit: **069 — Logging, Observability, Audit Integrity & Sensitive Telemetry**, including structured logging, exception capture, tracing, correlation IDs, log injection, retention, access controls, and alerting.
