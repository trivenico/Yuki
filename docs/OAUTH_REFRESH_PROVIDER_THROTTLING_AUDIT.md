# OAuth refresh transport discards throttling signals and provider error classes

## Finding

**Severity: Medium — provider throttling and definitive OAuth errors are collapsed into a generic failure, with no durable cooldown or retry-after scheduling.**

The pinned OAuth form transport rejects every non-2xx token response as the same `oauth_http_request_failed` error. It does not preserve the HTTP status, selected response headers such as `Retry-After`, or a bounded/sanitized OAuth error code. `OAuthTokenRefreshService.refresh_if_needed()` then catches that transport exception and maps it to `credential_refresh_failed`.

As a result, the caller cannot distinguish a rate limit (for example, HTTP 429) from an OAuth grant rejection such as `invalid_grant`, a transient server failure, or another HTTP error. No retry-after/cooldown state is recorded. Since each MCP token acquisition invokes the refresh service again while the credential remains inside the refresh-skew window, subsequent requests can repeatedly send refresh attempts during a provider throttle window.

This is separate from the ambiguous-response recovery issue: this finding concerns loss of useful status signals and lack of deferral for responses that are actually received. It does not recommend automatically retrying an ambiguous request.

## Source evidence

- `core/oauth_http_transport.py`, `PinnedOAuthHTTPTransport.request_form()`:
  - Reads a bounded response body for non-2xx responses but discards it.
  - Raises the same `OAuthHTTPError("oauth_http_request_failed")` for all non-2xx statuses.
  - Does not capture `response.status` or `Retry-After` before raising.
- `core/oauth_refresh_service.py`, `OAuthTokenRefreshService.refresh_if_needed()`:
  - Catches all exceptions from the transport, token validation, and vault rotation.
  - Returns only `OAuthRefreshError("credential_refresh_failed")`.
  - Does not classify a 429, apply a cooldown, or defer the next refresh attempt.
- `core/oauth_mcp_connection.py`, `OAuthMCPConnectionManager.create_client()`:
  - Calls `refresh_if_needed()` for each access-token acquisition.
  - Does not have a provider/account refresh cooldown or deferred retry state.
- `tests/test_oauth_http_transport.py`:
  - Verifies redirects and successful JSON behavior.
  - Does not assert distinct handling of 429, 5xx, `Retry-After`, or OAuth error documents.
- `tests/test_oauth_refresh_service.py`:
  - Covers one generic transport timeout and no retry within that invocation.
  - Does not verify a cooldown across subsequent calls after a received 429.

## Concrete behavior

1. Credential version N enters the refresh-skew window.
2. An MCP request invokes the token callback and submits a refresh request.
3. The provider responds with HTTP 429 and may include `Retry-After`.
4. The transport discards the status/header and raises `oauth_http_request_failed`.
5. The refresh service maps that to `credential_refresh_failed`.
6. The next MCP request sees the same credential still requiring refresh and submits another request immediately.
7. Repeated concurrent or sequential requests can continue hitting the provider during its throttle window.

A similar loss of classification occurs for a received `invalid_grant` or a 5xx response. The caller cannot select an appropriate recovery path from the exposed error.

## Impact

- Provider rate limits can be repeatedly hit by ordinary MCP request traffic.
- A temporary provider throttle can become a prolonged outage for all requests using the affected connection.
- Operators cannot distinguish invalid/revoked grants from transient provider throttling using the surfaced error.
- The system loses a provider-supplied scheduling signal that could reduce load and avoid unnecessary requests.

No token disclosure, cross-owner access, or automatic retry within a single refresh invocation is established by this finding.

## Recommended remediation

1. Preserve a small typed transport error model containing HTTP status, a validated bounded OAuth error code, and a parsed `Retry-After` value where present. Do not retain or expose raw response bodies.
2. Parse `Retry-After` as either delta-seconds or an HTTP date, clamp it to a configured maximum, and use a trusted clock. Ignore malformed values safely.
3. Persist a provider/account-scoped `retry_after` or cooldown deadline for definitive throttling responses. The next caller should fail fast or wait according to request policy rather than contacting the provider again.
4. Distinguish definitive `invalid_grant` from 429 and 5xx. A definitive grant rejection should transition the connection to a reconnect-required or provider-rejected state; a received 429 should defer until its cooldown expires.
5. For 5xx responses, do not blindly retry a refresh operation unless the provider's semantics establish that the request was not processed. Conservatively classify uncertain processing outcomes as outcome-unknown and use the recovery design from `docs/OAUTH_REFRESH_AMBIGUOUS_OUTCOME_RECOVERY_AUDIT.md`.
6. Add bounded exponential backoff with jitter only for retryable operations whose remote side-effect semantics are safe. Do not automatically replay a refresh token merely because a response was 5xx or a timeout occurred.
7. Expose sanitized reason codes and cooldown metadata to operators. Never log access tokens, refresh tokens, authorization headers, or raw provider response bodies.

## Regression tests

- Return HTTP 429 with `Retry-After: 30`; assert the transport preserves status and parsed delay and the refresh service records a cooldown.
- Call the refresh service again before the cooldown expires; assert no second provider request is sent.
- Test both delta-seconds and HTTP-date `Retry-After` formats, malformed values, past dates, and maximum clamping.
- Return a bounded OAuth JSON error with `error=invalid_grant`; assert it is classified distinctly and does not enter the throttle cooldown path.
- Return 500/503 and assert no blind replay occurs when provider processing outcome is uncertain.
- Ensure arbitrary provider response text and secrets are not surfaced in exception messages or logs.
- Verify cooldown keys are isolated by owner/provider/account and cleared or superseded safely after successful reauthorization.

## Related findings

- `docs/OAUTH_REFRESH_AMBIGUOUS_OUTCOME_RECOVERY_AUDIT.md` covers a provider-side refresh that may have succeeded while the response or local persistence was lost.
- `docs/OAUTH_CONCURRENT_REFRESH_SERIALIZATION_AUDIT.md` covers concurrent callers submitting the same refresh token.
- This report covers received HTTP error responses whose status and throttling metadata are currently discarded.

## Limitations

This is a source-level review. No live provider requests, rate-limit experiments, retry simulations, or runtime tests were executed. The exact meaning of `Retry-After` and the safe handling of provider error codes should be verified against each configured provider's documentation.
