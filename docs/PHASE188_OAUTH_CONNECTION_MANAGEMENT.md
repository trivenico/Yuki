# Phase 188 — Authenticated OAuth connection management

## Delivered

- Add a SQLite registry containing only owner/provider/account identifiers, granted scopes and connection timestamps. No access or refresh tokens are stored in this registry.
- Add authenticated GET /v1/integrations/oauth/connections to list the current owner's connections.
- Add authenticated DELETE /v1/integrations/oauth/connections/{provider_id}/{account_id} to initiate owner-authorized disconnect.
- Resolve disconnect services from trusted host configuration, never request-supplied endpoints.
- Preserve local credential deletion semantics when remote provider revocation is unavailable; report remote revocation separately.
- Add tests for owner isolation, non-secret projections, successful local disconnect, remote revocation failure, and failed disconnect handling.

## Explicitly not included

This phase does not expose authorization initiation/callback endpoints, provider discovery, or arbitrary provider configuration. The host must register a connection after completing the trusted OAuth authorization flow and provide a trusted provider-specific disconnect service resolver. No model-controlled provider IDs, URLs, credentials, or tool permissions are accepted.

## API

- GET /v1/integrations/oauth/connections
- DELETE /v1/integrations/oauth/connections/{provider_id}/{account_id}

All responses are no-store. Authentication uses the existing Yuki owner session.
