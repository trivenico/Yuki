# OAuth refresh can return a token with insufficient remaining lifetime for MCP dispatch

## Finding

**Severity: Low to Medium — a successfully refreshed access token can be returned to the MCP transport with almost no remaining validity.**

The OAuth token response validator accepts any positive `expires_in` value, including one second. The refresh service persists the resulting absolute expiry and reports refresh success. The MCP connection manager then reads the credential and returns its access token without checking that the credential is still valid at that point or that it has a minimum remaining lifetime.

The MCP client resolves its bearer token immediately before constructing and sending the HTTP request, which limits the exposure window. It does not remove it: a token with a one-second lifetime can expire between refresh completion, vault read, network setup, and provider validation. This is a bounded availability/reliability issue, not evidence that expired tokens are deliberately reused.

## Source evidence

- `core/oauth_token_response.py`, `validate_oauth_token_response()`:
  - Requires `expires_in` to be an integer in the range 1 through the configured maximum.
  - Computes `expires_at = int(clock()) + expires_in`.
  - Does not enforce a minimum lifetime appropriate for a token that must be persisted and then dispatched.
- `core/oauth_refresh_service.py`, `OAuthTokenRefreshService.refresh_if_needed()`:
  - Refreshes credentials inside the configured skew window.
  - Persists the validated expiry through `vault.rotate()`.
  - Returns metadata including `expires_at` and the new credential version.
  - Does not reject a grant whose remaining lifetime is below a dispatch safety margin.
- `core/oauth_mcp_connection.py`, `OAuthMCPConnectionManager.create_client()`:
  - Calls the refresh service and validates provider/account and scopes.
  - Re-reads the credential from the vault.
  - Validates owner/provider/account and scopes, but does not validate `credential.expires_at` or compare its version/expiry with the refresh result.
  - Returns the access token directly to the MCP client.
- `core/mcp_client.py`, `MCPHTTPClient._rpc()`:
  - Resolves the token immediately before invoking the pinned HTTP transport.
  - Does not have token expiry metadata and cannot independently enforce an expiry margin.
- `tests/test_oauth_token_response.py`, `tests/test_oauth_refresh_service.py`, and `tests/test_oauth_mcp_connection.py`:
  - Cover response validation, refresh, and lazy token retrieval.
  - Do not cover a one-second/near-expiry successful grant or expiry between refresh completion and token return.

## Concrete sequence

1. An existing credential enters the configured refresh-skew window.
2. The provider returns a structurally valid token response with `expires_in: 1`.
3. The response validator accepts it and calculates an expiry one second after its clock reading.
4. The refresh service persists the token and returns success.
5. The MCP manager re-reads the credential and checks identity and scopes only.
6. The manager returns the bearer token; request construction and network dispatch can consume the remaining validity window.
7. The MCP provider rejects the token as expired, producing a request failure even though the refresh operation was reported successful.

The same manager boundary trusts the refresh service's status for context/scopes but does not verify that the returned expiry is consistent with the vault row it subsequently reads.

## Impact and boundaries

- Short-lived provider grants can produce avoidable MCP request failures.
- A stale or inconsistent refresh result is not detected by comparing the reported credential version/expiry with the vault record.
- The issue is conditional on providers issuing very short-lived access tokens or a sufficiently delayed dispatch. It is not evidence that normal-lifetime tokens routinely expire in transit.
- The existing refresh-skew mechanism protects credentials that were already close to expiry before refresh; it does not impose a minimum lifetime on the replacement token.

## Recommended remediation

1. Define a configurable minimum usable lifetime for refreshed access tokens, based on expected transport latency and provider behavior. Reject or explicitly classify a grant that cannot meet it; do not assume that every provider permits a minimum TTL greater than one second.
2. After the manager re-reads the vault row, check `expires_at` against the current clock and require a safety margin before returning the bearer token.
3. Compare the refresh result's `credential_version` and `expires_at` with the re-read vault credential. Treat mismatches as a stale/inconsistent refresh result and fail closed.
4. Keep the token callback immediately adjacent to request dispatch. If the application requires a stronger guarantee, pass a short-lived credential handle/expiry-aware authorization object to the transport rather than a bare string; still acknowledge that remote processing can occur after local expiry.
5. Make the clock injectable at the manager boundary so expiry checks are deterministic and consistent with the refresh service.
6. Avoid automatically refreshing again when a newly issued token is already too short-lived: the provider may have consumed/rotated the refresh token. Route that condition through a provider-specific recovery path.

## Regression tests

- Validate that a response with a one-second lifetime is rejected or explicitly marked unusable under the configured minimum-lifetime policy.
- Simulate time advancing between token response validation, vault rotation, and manager token return; assert the manager refuses a credential below the remaining-lifetime margin.
- Return refresh metadata whose version or expiry differs from the vault row; assert fail-closed behavior.
- Verify ordinary tokens with sufficient remaining lifetime are still returned and token material remains absent from errors/logs.
- Verify that a too-short replacement does not trigger an automatic second refresh request.

## Related findings

- `docs/OAUTH_REFRESH_AMBIGUOUS_OUTCOME_RECOVERY_AUDIT.md` covers ambiguous remote refresh outcomes and must govern any recovery after a potentially consumed refresh token.
- `docs/OAUTH_REFRESH_PROVIDER_THROTTLING_AUDIT.md` covers discarded provider error and throttling metadata.
- This report is limited to minimum remaining access-token lifetime and the post-refresh token-return boundary.

## Limitations

This is a source-level review. No tests, runtime simulations, or live provider requests were executed. The practical severity depends on configured provider token lifetimes and actual request latency.
