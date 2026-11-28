# OAuth refresh can lose a provider-rotated token after an ambiguous local failure

## Finding

**Severity: High, conditional on provider refresh-token rotation — the refresh flow has no durable outcome-unknown state when the provider may have consumed the old refresh token but the replacement was not committed locally.**

The refresh service performs the remote token exchange before persisting the returned grant. If the provider rotates or invalidates the submitted refresh token, but the process crashes, loses the response, fails response validation, or cannot commit the vault rotation, the vault can continue to hold the old refresh token. The service exposes only a generic `credential_refresh_failed` error and records no durable indication that the remote outcome may have advanced.

A later request calls `refresh_if_needed()` again, reads the same expired credential and submits the same old refresh token. If the provider has already invalidated it, the connection may be unrecoverable through this path and require user reauthorization.

This is a single-request crash/recovery hazard. It is distinct from the concurrent duplicate-refresh race documented in `docs/OAUTH_CONCURRENT_REFRESH_SERIALIZATION_AUDIT.md`: no second simultaneous caller is needed.

## Source evidence

- `core/oauth_refresh_service.py`, `OAuthTokenRefreshService.refresh_if_needed()`:
  - Reads the credential and submits its refresh token to the provider.
  - Validates the returned document.
  - Only then calls `vault.rotate()`.
  - A broad exception handler maps transport, validation, and persistence failures to `OAuthRefreshError("credential_refresh_failed")`.
  - It does not persist an attempt identifier, pending state, response-received state, or outcome-unknown marker.
- `core/oauth_credential_vault.py`, `SQLiteOAuthCredentialVault.rotate()`:
  - Performs the local replacement transactionally.
  - A failed transaction leaves the previous credential row unchanged.
  - The schema stores credential values and metadata, but no refresh-operation state or recovery record.
- `core/oauth_mcp_connection.py`, `OAuthMCPConnectionManager.create_client()`:
  - Each token-provider invocation calls `refresh_if_needed()`.
  - A refresh exception becomes a generic `credential_not_available` failure for the MCP request.
  - There is no check for a prior ambiguous refresh attempt before the next call.
- `tests/test_oauth_refresh_service.py`:
  - The timeout test asserts one provider call and no vault rotation.
  - It does not make a second call after the ambiguous failure or assert that the old refresh token is quarantined from reuse.
  - No test simulates a provider response followed by a vault persistence failure or process interruption before commit.

## Failure sequence

1. The vault contains expired credential version N and refresh token R.
2. The service sends R to the provider.
3. The provider successfully processes the request and, under its configured rotation policy, issues replacement token R2 and invalidates R.
4. The response is lost, the process terminates, validation fails, or `vault.rotate()` fails before the local transaction commits.
5. The vault still contains version N and R.
6. The caller receives only `credential_refresh_failed`; there is no durable state indicating that R may have been consumed.
7. A later MCP request invokes the refresh callback again and resubmits R.
8. The provider may reject the stale token or apply its reuse-detection policy. The application has no stored R2 with which to recover.

The exact provider-side consequence depends on its token rotation and reuse-detection semantics. The local source-level gap exists regardless.

## Impact

- A transient crash or local storage failure can turn a recoverable refresh into a connection requiring reauthorization.
- Repeated MCP requests can repeatedly submit a refresh token whose provider-side status is unknown.
- Operators cannot distinguish an ordinary provider rejection from a response-lost-after-rotation or local-commit failure using the surfaced error.
- No cross-owner access or token disclosure is established by this finding.

## Recommended remediation

1. Introduce a durable refresh-operation state keyed by owner/provider/account and credential lifetime/version. Record a refresh attempt before the remote side effect, including a unique operation ID and the submitted credential generation (never the raw token).
2. Model explicit states such as `claimed`, `request_sent`, `response_received`, `committed`, `definitively_rejected`, and `outcome_unknown`. Persist transitions transactionally and make them recoverable after process restart.
3. On timeout, process interruption, response-validation failure after a potentially successful provider response, or vault commit failure, mark the operation outcome unknown where possible. Do not automatically resubmit the old refresh token while that state is unresolved.
4. Where provider capabilities permit, use documented rotation grace periods, token-family recovery, idempotency keys, or provider-specific reconciliation. Do not assume generic OAuth refresh endpoints support idempotent replay or token introspection.
5. If no safe provider-side recovery exists, surface a clear reconnect-required state and require reauthorization rather than silently retrying an old token indefinitely.
6. Preserve the vault's CAS and add durable credential-lifetime fencing so a late response cannot overwrite a replacement authorization.
7. Make refresh state visible in connection diagnostics without exposing token material. Provide a deliberate operator/user recovery action for unresolved attempts.

## Regression tests

- Simulate a provider that consumes R and returns R2, then force the vault commit to fail. Assert the operation becomes outcome-unknown and a second call does not submit R again.
- Simulate process restart after request dispatch but before response persistence. Assert recovery loads the pending attempt and does not blindly replay it.
- Simulate a definitive OAuth error response and distinguish it from a transport timeout or lost response.
- Simulate a response-validation failure after the provider may have consumed R and assert the state is not treated as an ordinary retryable failure.
- Verify recovery is scoped to the exact owner/provider/account and credential lifetime, and that a new authorization can supersede the old pending attempt safely.
- Verify diagnostic output contains reason codes only, never access tokens, refresh tokens, or raw provider response bodies.

## Related finding

`docs/OAUTH_CONCURRENT_REFRESH_SERIALIZATION_AUDIT.md` covers multiple callers concurrently submitting the same token. This report covers the independent sequential failure path in which one request may consume a token remotely but fail before local persistence.

## Limitations

This is a source-level review. No process-kill, SQLite fault-injection, provider integration, or live token-rotation tests were executed. Provider-specific recovery options and the consequences of replay depend on the configured provider's documented behavior.
