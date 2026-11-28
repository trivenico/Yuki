# OAuth refresh versus reauthorization ABA generation race

## Finding

**Severity: Medium — a stale refresh response can overwrite a newly authorized grant if a refresh token is reused across credential lifetimes.**

The credential vault initializes every newly inserted credential row at version 1. Refresh rotation uses a compare-and-swap predicate consisting of that version and an HMAC fingerprint of the refresh token. Disconnect deletes the row, and a subsequent authorization can insert a new row with the same version. If the new authorization grant contains the same refresh token value as the deleted grant, an in-flight refresh from the old credential lifetime can satisfy both predicates and update the new credential row.

This is a conditional ABA race. It depends on the provider issuing/reusing the same refresh-token value across the two grant lifetimes; it is not claimed to occur with every provider.

## Source evidence

- `core/oauth_credential_vault.py`, `SQLiteOAuthCredentialVault.store()`:
  - Inserts every new row with `version=1`.
  - It refuses a duplicate row while one exists, but a deleted row can be re-created.
- `core/oauth_credential_vault.py`, `SQLiteOAuthCredentialVault.disconnect()`:
  - Deletes the row. It does not retain a generation counter or tombstone after deletion.
- `core/oauth_credential_vault.py`, `SQLiteOAuthCredentialVault.rotate()`:
  - Reads the current row and accepts the refresh response if both `row.version == expected_version` and the stored refresh-token digest matches the expected digest.
  - It then writes the new access token, refresh-token digest, expiry and scopes, advancing the version by one.
- `core/oauth_refresh_service.py`, `OAuthTokenRefreshService.refresh_if_needed()`:
  - Captures the current version and refresh token before making a network request.
  - After the request returns, it passes those old values to `vault.rotate()`.
- `core/oauth_authorization_service.py`, `OAuthAuthorizationService.complete()`:
  - Stores a newly validated authorization-code grant through `vault.store()`.
- `core/oauth_token_response.py`, `validate_oauth_token_response()`:
  - Validates the shape and syntax of an optional refresh token, but does not require it to be different from a token issued for a previous authorization lifetime.

## Interleaving

Assume the old credential row has version 1 and refresh token R.

1. Refresh operation F reads version 1 and token R, then sends a request to the provider. Its network response is delayed.
2. Disconnect deletes the old credential row.
3. A new authorization completes for the same owner/provider/account. The vault inserts a new row with version 1.
4. The provider returns refresh token R for the new authorization grant (or otherwise reuses the same refresh token value).
5. F's delayed response returns. F calls `rotate(expected_version=1, expected_refresh_token=R, ...)`.
6. The new row has version 1 and the same refresh-token fingerprint, so the CAS predicates can both pass.
7. The old refresh response overwrites the newly authorized grant's access token, expiry, scopes and potentially refresh token.

The refresh-token fingerprint prevents the common case where the replacement grant has a different refresh token. It does not distinguish two separate credential lifetimes that happen to reuse the same token value.

## Impact

A stale refresh result can overwrite a newer authorization grant. Depending on provider semantics and the old refresh response, this can replace the new access token and grant metadata with values from the old refresh operation, or reintroduce an older refresh-token generation.

This is a lifecycle integrity issue. No cross-owner access or authorization bypass is established.

## Recommended remediation

1. Add a credential-lifetime identifier (random opaque generation ID) or a monotonically increasing generation counter that survives deletion and recreation.
2. Include that lifetime identifier in the credential record and require it in `rotate()`'s compare-and-swap predicate in addition to the row version and refresh-token fingerprint.
3. Do not reset the lifetime identifier when a credential row is deleted and re-created. A durable generation/tombstone table or a database-managed monotonic sequence can provide this property.
4. Have refresh capture the lifetime identifier with its initial credential read and pass it to `rotate()`.
5. Add a deterministic concurrency test: pause an old refresh after its initial read; delete the old credential; store a new credential with the same refresh token; resume the old refresh; assert the old response is rejected and the new grant remains unchanged.
6. Add a companion test where the replacement refresh token differs, preserving the existing fingerprint-based stale-response protection.

## Scope and confidence

The version reset and CAS predicates are directly established by the source. The overwrite interleaving is conditional on refresh-token reuse across a delete/recreate boundary. The reviewed token-response validator accepts a syntactically valid refresh token without enforcing uniqueness across authorization lifetimes. No provider-specific claim is made that a particular provider reuses tokens.

No runtime tests, provider integration tests, or concurrency stress tests were executed during this source review.
