# Phase 189 — Authenticated OAuth authorization lifecycle

## Delivered

- Add owner-authenticated POST /v1/integrations/oauth/providers/{provider_id}/authorize.
- Add owner-authenticated POST /v1/integrations/oauth/providers/{provider_id}/callback.
- Resolve authorization services only through a trusted host-provided provider resolver; requests cannot supply endpoints, issuers, client IDs, scopes, redirect URIs or credentials.
- Bind both authorization start and callback completion to the authenticated owner and the exact bearer session token. The durable transaction service enforces one-time state, PKCE, owner/session binding and expiry.
- Delegate callback validation, token exchange, OIDC nonce/issuer verification, provider identity verification and encrypted credential persistence to OAuthAuthorizationService.
- Register non-secret connection metadata only after the authorization service reports successful credential storage and its provider identity matches the trusted route.
- Bound callback request bodies and never echo callback URLs or authorization codes in API responses.
- Mount the authorization API optionally alongside the authenticated connection-management API.
- Add a trusted, authenticated provider catalog endpoint and an owner-facing browser panel for connect, status, scopes and disconnect.
- The browser uses text-only DOM rendering for provider metadata and never constructs provider endpoints. Configure each provider's exact registered redirect URI as `https://<trusted-yuki-origin>/oauth/callback/<provider_id>`. The web shell serves only validated provider-specific callback paths. The redirect URI must have no query string: OAuth callbacks are validated against the exact scheme, authority and path, and only `code`, `state`, and optional `iss` are accepted as callback parameters. The browser identifies the provider from the callback path and posts the original callback URL to the authenticated callback API using the initiating browser session.

## API contract

Provider catalog:
GET /v1/integrations/oauth/providers
Returns only bounded provider IDs, display names, descriptions and availability from a trusted host-injected catalog. The catalog never exposes endpoints, client secrets or tokens.

The browser workspace includes an Integrations panel to list connected accounts, initiate configured connections, and request owner-confirmed disconnect. Configure each provider's exact registered redirect URI as the Yuki web origin with `yuki_oauth_provider={provider_id}` in the query string; the browser posts the returned callback URL to the authenticated callback endpoint using the initiating session.

Start:
POST /v1/integrations/oauth/providers/{provider_id}/authorize
Body: empty JSON object
Response: authorization_url, expires_at

Complete:
POST /v1/integrations/oauth/providers/{provider_id}/callback
Body: callback_url
Response: connected, provider_id, account_id, granted scopes, connected_at

The browser must complete the provider redirect and submit the resulting callback URL using the same authenticated Yuki session that initiated authorization. A different, expired or revoked session cannot complete the transaction.

## Explicit boundaries

- No provider metadata or endpoint is accepted from the model or request body.
- No OAuth access or refresh token is returned to the browser or written to the connection registry.
- This phase does not configure a live provider, create OAuth client registrations, or enable any MCP tools.
- Host configuration must supply validated metadata, transport, token vault, identity verifier and optional OIDC verifier.
