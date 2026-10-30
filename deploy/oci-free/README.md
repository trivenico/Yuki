# OCI free-tier gateway container

This is a starter deployment for the Yuki gateway service. It is not yet the complete Yuki cloud runtime.

## Build and run

Run from this directory on the OCI A1 host:

```bash
export YUKI_GATEWAY_ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export YUKI_GATEWAY_OWNER_ID="your-stable-owner-id"  # choose once; all your devices must use this ID
export YUKI_GATEWAY_BOOTSTRAP_ENABLED=true  # only during operator enrollment
# Set it back to false and restart immediately after enrollment
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
- `GET /v1/agents`: list the seeded specialist profiles; owner device with `tasks:read` required.
- `POST /v1/tasks`: enqueue a text-only task for a registered agent; owner device with `tasks:write` and an `Idempotency-Key` header required.
- `GET /v1/tasks`: list the owner's task summaries; requires `tasks:read`.
- `GET /v1/tasks/{task_id}`: read the owner's task details; requires `tasks:read`.

The admin bootstrap token is a temporary single-operator provisioning mechanism, not end-user login or a replacement for an identity provider. Bootstrap routes are disabled by default; explicitly set YUKI_GATEWAY_BOOTSTRAP_ENABLED=true only during a short operator-controlled enrollment window, then set it back to false and restart. Keep the secret out of shell history, logs, source control and shared chat. The endpoint currently trusts the user_id supplied by the operator; do not delegate this token to desktop or CLI clients. Configure `YUKI_GATEWAY_OWNER_ID` to the same stable owner ID used when enrolling your CLI and desktop devices. Devices belonging to other IDs cannot access the task runtime.

## Runtime safeguards

- Runs as an unprivileged UID.
- Read-only container root filesystem.
- Drops Linux capabilities and disallows privilege escalation.
- Persistent named volume for SQLite state.
- Localhost-only host port binding.
- Docker restart policy and health check.
- Existing SQLite workflow store and seeded 21-profile starter agent registry are shared with the device database.
- Task APIs are owner-scoped and require `tasks:read` / `tasks:write`; task creation is idempotency-key protected and accepts bounded text-only requests.
- Resource ceiling is a host protection guardrail, not an application agent/task quota.

## Important gaps before public production use

This HTTP server is an early internal gateway. It bounds concurrent request workers (default 32) and request socket read timeouts, but it has no built-in TLS, user login/OIDC, rate limiting, CSRF/origin policy, refresh-token rotation, audit export, request correlation, distributed database, or backup automation. Do not expose the admin routes to the public internet. The next milestone is proper OIDC/user identity, CLI/desktop client credential storage, scoped API middleware, reverse proxy/TLS, rate limiting, and automated backup/restore tests. The task API only enqueues work; a separately running Yuki specialist worker must be configured against the same SQLite database to execute tasks.
