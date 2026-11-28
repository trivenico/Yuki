# OAuth refresh concurrency can race rotating refresh-token grants

## Finding

**Severity: High, conditional on provider refresh-token rotation semantics — concurrent requests can submit the same refresh token because refresh coordination is not serialized.**

`OAuthTokenRefreshService.refresh_if_needed()` reads the credential and, when it is inside the refresh-skew window, immediately sends a provider refresh request. The service has no per-owner/provider/account lock, single-flight mechanism, or durable refresh lease. The credential vault's compare-and-swap protects the local row from accepting more than one response for the same version, but it does not serialize requests at the OAuth provider.

If the provider uses rotating, single-use refresh tokens, two concurrent requests can submit the same refresh token. Provider behavior varies: one request may invalidate the token used by the other, a reuse-detection policy may revoke a token family, or one response may become stale before it is persisted. In those cases, local CAS can reject the losing response while the provider-side token state has already advanced or invalidated the token that the vault retained.

This is a provider-dependent concurrency hazard, not a claim that every provider mishandles concurrent refresh requests.

## Source evidence

- `core/oauth_refresh_service.py`, `OAuthTokenRefreshService.refresh_if_needed()`:
  - Reads the current credential.
  - Checks expiry against the configured refresh skew.
  - Sends `request_form()` with the current refresh token.
  - Only after the network request and response validation does it call `vault.rotate()`.
  - No lock, lease, or in-flight refresh coordination is present in the service.
- `core/oauth_credential_vault.py`, `SQLiteOAuthCredentialVault.rotate()`:
  - Uses a transaction and checks the expected credential version and refresh-token HMAC fingerprint.
  - Rejects a stale local response.
  - Cannot undo or serialize provider-side refresh-token rotation that already happened.
- `core/oauth_mcp_connection.py`, `OAuthMCPConnectionManager.create_client()`:
  - The just-in-time token callback invokes `refresh_if_needed()` on each token acquisition.
  - There is no request-level refresh deduplication in the manager.
- `tests/test_oauth_refresh_service.py`:
  - Tests a single refresh and a single ambiguous transport failure.
  - Does not exercise two simultaneous refresh calls against the same credential.
- `docs/PHASE177_OAUTH_REFRESH_LIFECYCLE.md`:
  - Describes single-attempt refresh and CAS rotation.
  - Explicitly leaves scheduling/background coordination out of scope; the reviewed request path also has no concurrent refresh gate.

## Concrete interleaving

Assume credential version 4 contains refresh token R and is within the refresh-skew window.

1. MCP request A calls `refresh_if_needed()` and reads version 4 / token R.
2. MCP request B calls the same service before A finishes and also reads version 4 / token R.
3. A sends a refresh request containing R.
4. B sends a second refresh request containing R.
5. The provider processes the requests according to its refresh-token rotation/reuse policy. It may issue distinct replacement tokens, invalidate R after the first use, or revoke a token family after detecting reuse.
6. One local response may successfully rotate version 4 to version 5.
7. The other local response is rejected by the vault CAS as stale, or its provider request fails.
8. The vault's version-5 refresh token may nevertheless be invalid at the provider if the provider-side concurrent sequence invalidated it.

The local CAS is necessary and correctly prevents a stale response from overwriting the current row. It is not sufficient to make a non-idempotent remote refresh operation safe under concurrency.

## Impact

- A valid OAuth connection can become unusable during a burst of simultaneous MCP requests near token expiry.
- Providers with refresh-token reuse detection may invalidate a grant or token family.
- The failure can affect all requests using that owner/provider/account connection until the user reauthorizes.
- The exact impact depends on provider-specific refresh-token rotation and reuse-detection behavior.

No cross-owner access is established by this finding.

## Recommended remediation

1. Introduce a single-flight refresh coordinator keyed by owner/provider/account so concurrent callers await the same in-flight refresh result instead of submitting the same refresh token repeatedly.
2. If multiple application processes or hosts can share the credential vault, use a durable database-backed refresh lease/claim with an owner, lease expiry, and fencing generation. A process-local mutex alone is insufficient in a multi-process deployment.
3. After waiting for another refresher, re-read the vault. If the credential version advanced and the credential is fresh, return its metadata without calling the provider.
4. Ensure lease expiry and crash recovery do not cause an immediate duplicate submission of a refresh token whose provider outcome is ambiguous. Preserve an explicit outcome-unknown/recovery state where the provider may have rotated the token but the response was not durably stored.
5. Do not blindly retry `invalid_grant`, timeouts, or other ambiguous failures. Recovery should re-read current local state and use provider-specific recovery/re-authorization behavior where supported.
6. Keep the vault CAS as a second line of defense, and combine it with the durable credential-lifetime generation proposed in the separate ABA-race audit.
7. Add deterministic tests with a barrier-controlled fake transport:
   - Start two callers against the same expiring version-4 credential.
   - Hold the first refresh request while the second caller arrives.
   - Assert only one provider request is issued.
   - Assert both callers receive metadata for the same committed version.
   - Repeat across two coordinator instances sharing the same SQLite database to verify cross-process lease behavior.
   - Simulate a crash/timeout after provider-side rotation but before local CAS and assert the system does not blindly resubmit the old refresh token.

## Operational observability

The refresh service currently catches broad exceptions and returns the stable `credential_refresh_failed` error. This avoids leaking provider details, but does not distinguish provider rejection, transport timeout, stale CAS, or lease contention to operators. Add structured, secret-free internal reason codes and metrics for refresh attempts, coalesced callers, stale CAS conflicts, provider throttling/rejection, and outcome-unknown states. Never log access tokens, refresh tokens, or raw provider response bodies.

## Limitations

This is a source-level concurrency finding. No concurrent tests, provider integration tests, or live token-rotation experiments were run. Whether duplicate refresh calls invalidate a grant depends on the configured provider's documented semantics. The source does establish that concurrent calls are not serialized before the remote side effect and that local CAS occurs only after that side effect.
