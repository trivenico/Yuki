# Phase 227 — Authenticated provider profile management API

## Scope

Adds an authenticated owner-scoped HTTP API over the persistent provider profile
store introduced in Phase 225 and the secure CLI lifecycle introduced in Phase
226. The API is intended to be consumed by both the CLI and the future desktop
application, avoiding separate credential/profile management implementations.

## Routes

- `GET /v1/provider-profiles` — list the authenticated owner's safe metadata.
- `POST /v1/provider-profiles` — create or rotate a profile. Requires explicit
  `confirm_cloud: true`; the API key is accepted only for vault insertion.
- `GET /v1/provider-profiles/active` — read the owner's selected profile.
- `PUT /v1/provider-profiles/active` — persist the owner's selected profile.
- `POST /v1/provider-profiles/{name}/verify` — verify local vault presence only;
  no provider network request is made.
- `DELETE /v1/provider-profiles/{name}` — delete the owner's profile and vault
  credential, and clear its active selection.

Owner identity is derived exclusively from the authenticated bearer session.
Request-supplied owner IDs are rejected. Responses never include credential
material and use no-store cache headers. The profile store enforces HTTPS,
exact approved-host matching and vault-only secret storage.

## Explicit limitations

This API manages owner-scoped profile metadata and selection; it does not yet
activate a different provider adapter for each user's chat requests. The current
conversation execution loop is shared by the server runtime, so mutating its
provider would create cross-user selection leakage. Per-owner execution context
and request-scoped adapter construction must be implemented before selected
profiles affect chat. Connection-time DNS/IP pinning, private-address rejection and redirect blocking
are implemented in the preceding provider transport phase. Network-layer egress
restrictions remain required as defense in depth. The deployment must terminate
TLS before accepting API-key request bodies and redact request bodies from access,
error and tracing telemetry. Apply per-owner rate limits to create/rotate and
verify operations at the API gateway or authenticated middleware layer.

This is a draft engineering phase, not a production release.
