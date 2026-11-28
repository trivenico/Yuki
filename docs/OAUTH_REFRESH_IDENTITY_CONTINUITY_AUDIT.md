# OAuth Refresh Identity Continuity Audit

## Scope

Reviewed at PR #1346 head `317718ed75f883aa1cfd6baaa11efffe98ac9736`:

- `core/oauth_refresh_service.py`
- `core/oauth_authorization_service.py`
- `core/oauth_token_response.py`
- `core/oauth_credential_vault.py`
- `tests/test_oauth_refresh_service.py`
- `tests/test_oauth_token_response.py`

Source-level review only. No runtime tests were executed.

## Existing authorization-time identity binding

The authorization-code completion flow validates the token response, verifies OIDC identity when configured, then invokes the configured provider identity verifier against the access token before storing the credential.

This establishes an identity check at initial authorization time.

## Finding: refresh replaces the verified access token without revalidating provider identity

`OAuthTokenRefreshService.refresh_if_needed()` retrieves the current credential, exchanges its refresh token, validates the response shape and scope subset, and calls `credential_vault.rotate()` with the new access token.

Unlike authorization completion, the refresh service has no identity-verifier dependency and performs no provider-account identity verification before rotating the vault row.

Therefore, the access token that replaces the previously identity-verified token is not independently checked to ensure it still represents the configured provider account.

### Impact and limits

If a provider, provider account configuration, or token endpoint issues a refresh result whose access token represents a different subject/account, the refresh service accepts and persists it so long as its syntactic, lifetime, and scope checks pass. Subsequent MCP calls can then use a credential whose identity no longer matches the host-configured account binding.

The token endpoint is reached over the configured trusted transport and endpoint. This review does not demonstrate an external attacker able to alter a valid provider response, nor establish that a currently integrated provider changes subject during refresh. The issue is a missing identity-continuity enforcement boundary against provider misconfiguration, account reassignment, or unexpected provider behavior.

## Related OIDC refresh-response gap

The shared token-response validator permits an optional compact `id_token` field. The refresh service does not inspect or verify that field before rotating credentials. It neither rejects a returned ID token when OIDC is not configured nor verifies issuer, audience, nonce/refresh context, subject continuity, or signature when one is returned.

The service currently does not persist the ID token, so the primary issue is not storage of an unverified ID token. It is that an OIDC refresh response can carry identity material that is silently ignored, while the access token is accepted without identity continuity verification.

## Recommendations

1. Inject a trusted provider identity verifier into the refresh service, using the same provider/account-specific binding as authorization completion.
2. Verify every newly issued access token against the expected provider account before vault rotation.
3. Bind the verifier to a stable expected subject or account identity captured at initial authorization; do not allow a refresh response to redefine that expected identity.
4. When OIDC refresh responses include `id_token`, verify signature, issuer, audience/authorized party, expiry, and subject continuity using a refresh-appropriate policy. Do not blindly reuse the authorization nonce requirement for refresh ID tokens; define the correct protocol context.
5. If OIDC is not configured, reject unexpected `id_token` values on refresh rather than silently ignoring them.
6. Add tests proving identity mismatch and invalid refresh ID tokens fail before `vault.rotate()`, and that a valid same-subject refresh succeeds.
7. Preserve the existing single-attempt/ambiguous-outcome behavior: identity validation failure after a provider may have rotated its refresh token must be treated as an outcome-unknown case, not retried with the old token. This overlaps the existing refresh ambiguous-outcome recovery audit and should use that recovery mechanism rather than introduce a second retry policy.

## Severity and confidence

**Severity: Medium.** This is a credential identity-continuity gap on a security-sensitive token replacement path. Actual exploitability depends on provider response behavior and whether the provider can issue a token for a different subject under the same refresh grant.

**Confidence: High** that refresh does not invoke identity verification and rotates the access token based on response shape/scope validation alone.

## Limitations

- Source inspection only; no runtime or provider integration tests were executed.
- No claim is made that a configured provider currently returns a cross-subject refresh token.
