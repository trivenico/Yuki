# OAuth refresh versus disconnect generation race

## Finding

**Severity: High — a stale disconnect can delete a successfully refreshed credential**

The credential vault implements version-fenced deletion, but the production disconnect coordinator does not pass the version it read. A refresh that completes while disconnect is waiting on the provider's revocation endpoint can therefore have its newer credential generation deleted by the older disconnect operation.

## Source evidence

- `core/oauth_disconnect_service.py`, `OAuthDisconnectService.disconnect()`:
  - Reads the credential using `vault.get(...)`.
  - Uses the returned refresh token (or access token) for a potentially blocking remote revocation request.
  - Calls `vault.disconnect(owner_id=..., provider_id=..., account_id=...)` without `expected_version`.
- `core/oauth_refresh_service.py`, `OAuthTokenRefreshService.refresh_if_needed()`:
  - Reads the current credential and its version.
  - Performs the provider refresh request.
  - Calls `vault.rotate(... expected_version=current.version, expected_refresh_token=current.refresh_token, ...)`.
- `core/oauth_credential_vault.py`, `SQLiteOAuthCredentialVault.rotate()`: atomically advances the credential version only when the stored version and refresh-token fingerprint still match.
- `core/oauth_credential_vault.py`, `SQLiteOAuthCredentialVault.disconnect()`: supports `expected_version`; when supplied, the SQL DELETE includes the version predicate. When omitted, it deletes the row unconditionally.
- `tests/test_oauth_credential_vault.py`: tests version-fenced deletion directly.
- `tests/test_oauth_disconnect_service.py`: its fake vault accepts arbitrary kwargs, but the service tests do not assert that disconnect passes the observed credential version.

## Interleaving

1. Disconnect D reads credential generation N and retains its refresh token for revocation.
2. D sends the remote revocation request and blocks awaiting the provider.
3. Refresh R reads generation N, completes the refresh-token exchange, and successfully rotates the vault row to generation N+1.
4. D's revocation request returns (successfully or with an ambiguous failure).
5. D calls the vault's unconditional disconnect operation.
6. The vault deletes generation N+1, despite D having only read generation N.

The refresh CAS does not prevent this: it protects the rotate operation from stale writers, but the later unconditional DELETE is a separate operation and has no generation predicate.

## Impact

A valid, newly refreshed credential can be removed by a disconnect operation that began against an older generation. The user can lose a working integration unexpectedly. The registry is subsequently removed by the API, so the final state may look like a clean disconnect even though the operation revoked a different, older token generation than the credential it deleted.

This is a concurrency defect in the normal lifecycle; it does not require attacker-controlled provider configuration or identity.

## Recommended remediation

1. Pass `credential.version` as `expected_version` to `vault.disconnect()`.
2. Treat a false return from version-fenced deletion as a stale-generation conflict, not as successful disconnect. Do not remove the registry row for a generation that was not deleted.
3. Return a stable conflict/retryable result to the API (for example, a sanitized 409) and let the owner retry after the current connection state is re-read.
4. Consider whether remote revocation should occur before or after a generation claim. A version-fenced local delete prevents deleting a newer local credential, but cannot undo provider-side effects if revoking an old refresh token also invalidates a rotated token family. Provider-specific semantics must be considered; do not automatically retry ambiguous revocation.
5. Add a service-level test asserting that `expected_version` equals the credential version returned by `get()`.
6. Add a deterministic concurrency test: pause disconnect after its initial read while refresh rotates N to N+1; resume disconnect and assert N+1 remains in the vault and the disconnect result is a conflict.
7. Add a test where the version-fenced delete loses to a concurrent replacement and verify the API does not remove the corresponding registry generation.

## Scope and confidence

The race is directly supported by the current source code and the vault's two deletion modes. It is not an observed production incident. The vault already contains the primitive needed to prevent the local credential deletion, but the disconnect coordinator does not use it.

No runtime tests were executed during this source review.
