# OCI free-tier gateway container

This is a starter deployment for the Yuki gateway service. It is not yet the complete Yuki cloud runtime.

## Build and run

Run from this directory on the OCI A1 host:

```bash
export YUKI_GATEWAY_ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 yuki-gateway
```

The compose file binds the service to host loopback on port 8765. It is not directly internet-facing. Put a reviewed TLS reverse proxy in front of it before remote access. Do not open port 8765 in OCI security lists.

## Current API

- `GET /healthz`: process health.
- `POST /v1/admin/devices`: issue a device credential; requires the operator bootstrap bearer token.
- `DELETE /v1/admin/devices/{device_id}`: revoke a device; requires the operator bootstrap bearer token.
- `GET /v1/me`: inspect the authenticated device identity; requires `profile:read`.
- `GET /v1/devices`: list devices belonging to the authenticated user; requires `devices:read`.

The admin bootstrap token is a temporary single-operator provisioning mechanism, not end-user login or a replacement for an identity provider. Keep it out of shell history, logs, source control and shared chat. Rotate it after enrollment. The endpoint currently trusts the user_id supplied by the operator; do not delegate this token to desktop or CLI clients.

## Runtime safeguards

- Runs as an unprivileged UID.
- Read-only container root filesystem.
- Drops Linux capabilities and disallows privilege escalation.
- Persistent named volume for SQLite state.
- Localhost-only host port binding.
- Docker restart policy and health check.
- Resource ceiling is a host protection guardrail, not an application agent/task quota.

## Important gaps before public production use

This HTTP server is an early internal gateway. It has no built-in TLS, user login/OIDC, rate limiting, CSRF/origin policy, refresh-token rotation, audit export, request correlation, distributed database, or backup automation. Do not expose the admin routes to the public internet. The next milestone is a proper user identity flow, scoped API middleware, reverse proxy/TLS, rate limiting and automated backup/restore tests.
