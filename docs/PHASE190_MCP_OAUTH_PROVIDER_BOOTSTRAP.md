# Phase 190 — Trusted MCP OAuth provider bootstrap

## Delivered

- Compose protected-resource discovery, authorization-server metadata validation, explicit client registration, and the existing owner/session-bound OAuth authorization service.
- Require host-owned provider/account IDs, resource URI, provider-specific callback URI, scopes, deployment type, and explicit metadata, registration, and authorization host allow-lists.
- Verify authorization and token endpoint hosts before registration or authorization can proceed.
- Support pre-registered clients, CIMD, and explicitly allow-listed DCR.
- Keep metadata discovery, registration, and token exchange on separate injected transports.
- Return a prepared authorization service without starting authorization, exchanging a code, persisting credentials, registering a connection, or enabling MCP tools.

## Security boundaries

- No request- or model-supplied URLs, callback URIs, scopes, client IDs, or allow-lists.
- Callback URI must use the provider-specific path /oauth/callback/{provider_id}.
- Metadata discovery is GET-only and host-allow-listed.
- DCR requires an explicit registration host allow-list.
- Token exchange retains existing PKCE, issuer, resource, identity, optional OIDC, and encrypted-vault checks.
- Provider activation and MCP tool exposure remain separate owner-approved steps.

## Integration

Host startup code creates one OAuthProviderBootstrapConfig per provider and calls MCPProviderOAuthBootstrap.prepare. The returned authorization_service can be supplied to the existing authenticated authorization API's trusted provider resolver. Do not publish a provider in the catalog until endpoints, callback approval, scopes, and registration method are verified.
