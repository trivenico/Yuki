# OAuth Provider Configuration Rotation and Credential Binding Audit

## Scope

Source review at PR #1346 head `1960253709a8c3f5e3257905acab11e14d497bc5`. Reviewed provider bootstrap configuration, prepared-provider registry, authorization service, credential vault, refresh service, and OAuth-backed MCP connection manager. This report focuses on provider configuration changes under a stable provider ID and does not repeat the DCR lifecycle finding.

## Finding: persistent credentials are not bound to issuer/resource/client configuration generations

**Severity: Medium — stale credential reuse and configuration-isolation risk.**

The credential vault's primary key is `(owner_id, provider_id, account_id)`. The stored row contains tokens, expiry, scopes, version, and connection timestamps, but no issuer, resource identifier, client ID, token endpoint, redirect URI, or configuration fingerprint.

At the same time:

- `OAuthProviderBootstrapConfig` permits the host to change `expected_issuer`, `resource_uri`, redirect URI, scopes, and registration configuration while retaining the same `provider_id` and `account_id`.
- `MCPProviderOAuthBootstrap.prepare()` builds a new authorization service using the newly discovered issuer/resource/client configuration, but it does not compare that configuration with existing vault rows or invalidate/partition credentials.
- `OAuthTokenRefreshService` loads credentials solely by owner/provider/account, then sends the stored refresh token to its currently configured token endpoint using its currently configured client ID. It validates the response against the currently configured issuer/resource, but it does not establish that the stored refresh token was originally issued under that configuration.
- `OAuthMCPConnectionManager` loads the same credential by owner/provider/account and returns its access token without checking issuer/resource/configuration provenance. Its MCP resource endpoint is supplied separately to `create_client()`.
- The connection registry uses the same owner/provider/account key and stores scopes only, so it cannot detect a provider configuration generation mismatch.

Thus a host configuration rotation that keeps the same provider ID can leave old grants eligible for use by the newly prepared provider service. If the old grant is still unexpired, the connection manager can return the old access token directly. If refresh is required, the refresh service can submit the old refresh token to the new token endpoint/client configuration. A different issuer will commonly reject the token, but rejection is not a binding guarantee; resource/client changes within one issuer or provider-side credential reuse can make the stale grant usable in the new configuration context.

The code does bind pending authorization transactions to issuer and resource and checks those values on callback completion. That protects in-flight authorization transactions, but does not bind already persisted credentials to the same configuration identity.

## Evidence

- `core/oauth_credential_vault.py`: schema primary key is owner/provider/account; stored metadata omits issuer/resource/client/config generation.
- `core/mcp_oauth_provider_bootstrap.py`: provider configuration includes resource URI, redirect URI, scopes and issuer selection; `prepare()` composes a new service without consulting existing credentials or a configuration-generation store.
- `core/oauth_authorization_service.py`: newly completed grants are stored using only owner/provider/account; issuer/resource are used for transaction and token-response validation but are not persisted with the grant.
- `core/oauth_refresh_service.py`: refresh lookup uses owner/provider/account, then uses the service instance's current token endpoint, client ID, issuer and resource.
- `core/oauth_mcp_connection.py`: token lookup is owner/provider/account only; no issuer/resource provenance check occurs before returning the access token.
- `core/oauth_connection_registry.py`: connection metadata is keyed by owner/provider/account and has no provider configuration generation.
- `core/oauth_provider_registry.py`: prepared services are immutable in-memory mappings keyed only by provider ID; this does not provide durable credential migration or invalidation.

## Recommended remediation

1. Define a canonical provider configuration identity/fingerprint over at least provider ID, issuer, resource, client ID, redirect URI, and relevant authorization/token endpoint identity. Include a monotonically increasing generation or immutable configuration ID.
2. Persist the configuration identity/generation with every credential and connection record. Ensure AEAD associated data or equivalent integrity binding covers the generation where appropriate.
3. Require refresh and MCP access-token retrieval to match the active provider configuration identity before using a credential. Fail closed on mismatch.
4. Make provider rotation explicit: either require reauthorization, or perform a controlled migration only when the host can prove compatibility. Do not silently relabel old credentials as belonging to a new issuer/resource.
5. Bind the MCP resource endpoint to trusted provider configuration rather than accepting an unrelated endpoint at each connection construction, or validate the endpoint against the active provider configuration before creating the client.
6. Add tests for issuer rotation, resource rotation, client ID rotation, redirect URI rotation, old unexpired access tokens, and refresh of a grant created under an earlier configuration generation.

## Non-findings and boundaries

- Authorization transaction records already bind issuer and resource, and callback completion checks both values.
- Token-response validation checks the issuer/resource expected by the current authorization or refresh service; the missing control is provenance binding between the stored grant and that service configuration.
- Provider IDs and account IDs are host-owned configuration, not model-supplied request values. Exploitability therefore depends on a host configuration change or unsafe reuse of a provider ID, not arbitrary user selection of endpoints.
- No claim is made that every provider accepts an old token under a changed configuration. The defect is the absence of a local invariant that prevents stale grants from being used across configuration generations.
- No runtime code was changed and no tests were executed during this source review.
