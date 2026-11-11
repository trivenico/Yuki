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

## API contract

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
