# OAuth Concurrent Refresh Single-Flight and Token-Reuse Audit

## Scope

Source review at PR #1346 head `007b247d451d66207098f300c35fee60a6f4c4aa`. Reviewed:

- `core/oauth_refresh_service.py`: `OAuthTokenRefreshService.refresh_if_needed()`
- `core/oauth_mcp_connection.py`: lazy access-token callback created by `OAuthMCPConnectionManager.create_client()`
- `core/mcp_client.py`: per-RPC authorization-header acquisition
- `core/oauth_credential_vault.py`: post-response refresh compare-and-swap
- `tests/test_oauth_refresh_service.py`
- `tests/test_oauth_credential_vault.py`
- Existing refresh/disconnect generation and reauthorization ABA reports

No runtime tests were executed; this is a source-level review.

## Finding: concurrent requests can submit the same rotating refresh token before the vault CAS

**Severity: Medium — concurrent refresh attempts can trigger provider refresh-token reuse detection and invalidate a grant.**

The manager creates an access-token callback that invokes `refresh_if_needed()` for every MCP RPC. The refresh service:

1. Reads the current credential and its version from the vault.
2. If it is near expiry, sends a refresh-token grant to the provider.
3. Validates the returned token document.
4. Only after the remote request returns, calls `vault.rotate()` with the version and refresh-token fingerprint it originally read.

The vault's `BEGIN IMMEDIATE` and version/fingerprint compare-and-swap correctly ensure that only one response can update a given stored generation. However, the provider requests occur before that transaction and are not protected by a per-account refresh lock, durable refresh lease, or single-flight coordinator.

The MCP client calls the token provider independently for each RPC. Two concurrent RPCs can therefore enter the refresh service at the same time, both read generation N, and both submit the same refresh token R before either response has rotated the vault.

## Interleaving

1. Credential generation N is within the configured refresh skew.
2. MCP request A calls the access-token provider; refresh operation A reads generation N and refresh token R.
3. Before A's provider response is committed to the vault, MCP request B calls the provider; refresh operation B also reads generation N and refresh token R.
4. Both send `grant_type=refresh_token` using R to the provider.
5. Provider A succeeds and rotates R to R2; its response is persisted as generation N+1.
6. Provider B's request is now a reuse of the old token R. Depending on provider policy, it may return `invalid_grant`, or trigger refresh-token-family reuse detection and revoke the active grant/token family.
7. The vault CAS prevents B from overwriting generation N+1, but it cannot undo the second remote refresh request or any provider-side reuse response.

The exact remote consequence is provider-specific. The repository does not establish that every configured provider uses refresh-token rotation or family-wide reuse detection. The local absence of request serialization is directly visible in the source.

## Impact and boundaries

- Concurrent MCP calls can cause avoidable refresh failures even when one call successfully refreshes the grant.
- For providers that revoke a token family after refresh-token reuse, a benign burst of concurrent MCP requests can invalidate the newly rotated credential and interrupt the integration until reauthorization.
- A stale response cannot overwrite a newer vault generation when the CAS predicates differ; that protection remains effective.
- This finding does not establish cross-owner credential access or an authorization bypass.
- This is distinct from `OAUTH_REFRESH_DISCONNECT_GENERATION_RACE_AUDIT.md` (stale disconnect deleting a newer local credential) and `OAUTH_REFRESH_REAUTHORIZATION_ABA_RACE_AUDIT.md` (old refresh response matching a recreated row after delete/reinsert with a reused refresh token). It concerns duplicate provider-side refresh requests within one live credential generation.

## Existing test coverage and gap

The refresh-service tests cover a single refresh, omitted replacement refresh-token behavior, widened scopes, missing refresh credentials, and a single ambiguous transport failure. Vault tests cover compare-and-swap behavior and stale generations.

The reviewed tests do not run two refresh-service invocations concurrently against one credential, do not assert that only one provider request is in flight, and do not model a provider that invalidates a token family after reuse.

## Recommended remediation

1. Add a per-owner/provider/account single-flight refresh coordinator so concurrent callers await one in-progress refresh result instead of independently submitting the same refresh token.
2. If multiple processes can refresh the same grant, use a durable lease/claim in shared storage with owner, credential-lifetime ID, version, lease expiry, and fencing token. A process-local mutex is insufficient across processes.
3. Do not hold a SQLite write transaction open across the provider network request. Claim the refresh generation in a short transaction, perform one remote request under the durable lease, then commit the response with the existing version/lifetime CAS.
4. Define crash and ambiguity semantics. If the process dies after the provider rotates the token but before persistence, mark the refresh outcome uncertain and require provider-specific recovery or reauthorization; do not blindly retry the old refresh token.
5. After waiting for an in-flight refresh, re-read the vault and return the current generation's metadata/token rather than starting another refresh.
6. Bind the refresh lease to a credential-lifetime identifier so a delayed lease holder cannot publish into a disconnected/re-authorized grant. This complements, but does not replace, the ABA generation fix.
7. Add deterministic barrier-based tests with two concurrent callers: exactly one provider refresh request, both callers observe the same persisted generation, a stale lease holder cannot commit, lease expiry/crash is reconciled, and a disconnect/reconnect creates a new fenced lifetime.

## Conclusion

The vault's compare-and-swap protects local persistence but is too late to serialize the side effect at the OAuth provider. Concurrent MCP RPCs can independently submit the same near-expiry refresh token. A shared single-flight or durable refresh-lease mechanism is needed to prevent duplicate refresh requests and provider-specific token-reuse consequences.
