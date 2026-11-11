# Phase 191 — Prepared OAuth provider registry

## Purpose

Bridge the trusted provider bootstrap to the existing authenticated OAuth API without introducing a second authorization path or exposing provider secrets/configuration.

## Integration

Construct `PreparedOAuthProviderRegistry` from successfully prepared provider objects and trusted presentation metadata. Pass:

- `registry.authorization_service_for_provider` as `AuthenticatedOAuthAuthorizationAPI.authorization_service_for_provider`.
- `registry.provider_catalog` as `AuthenticatedOAuthAuthorizationAPI.provider_catalog`.

The registry accepts only prepared provider objects, requires exact provider-key identity matches, requires one trusted presentation per provider, and rejects duplicate/missing configuration through mapping-key validation.

## Exposure boundary

The catalog returns only provider ID, display name, bounded description, and availability. It never returns issuer, resource endpoint, authorization/token/registration endpoints, client ID, redirect URI, scopes, access tokens, or refresh tokens.

The registry is immutable after construction. Unknown provider IDs return no service. It does not connect providers, initiate authorization, create registrations, or enable MCP tools by itself.
