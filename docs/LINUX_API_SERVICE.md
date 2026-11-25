# Linux production API service

Yuki already provides the ASGI conversation runtime in `api.runtime` and the
browser/API composition in `scripts.run_web.create_app`. The packaged
`api.production_service` entry point supervises that existing runtime; it
does not introduce another framework or bypass the production bootstrap gate.

## Deployment-owned bootstrap

Set `YUKI_ENVIRONMENT=production` and
`YUKI_API_BOOTSTRAP=module:callable`. The callable must return a fully
composed `ConversationRuntime` created through the existing production web
composition with a real, ready `ProductionSecurityRuntime`. The launcher
checks both types, verifies that the context is the exact context owned by
that security runtime, and calls its readiness check before starting Uvicorn.

The deployment factory must obtain trusted human identity/session resolution,
policy and its pinned revision, PostgreSQL repositories, durable authorization
audit, and an independently administered audit checkpoint anchor from
deployment-owned integrations. Secrets belong in OCI Vault or an equivalent
secret manager. Do not derive principal, tenant, permissions, agent identity,
or policy revision from request bodies, environment variables, YAML, prompts,
or model output. The launcher intentionally has no default factory.

## Network and process boundary

The API binds to loopback only. Put a separately managed TLS reverse proxy in
front of it on a private network; configure trusted hosts, forwarded headers,
rate limits, request-size limits, and proxy identity at that boundary. This
entry point disables Uvicorn proxy-header trust because it does not itself
validate a proxy allowlist. Do not set a public bind address or expose port
8000 to the internet.

Install `deploy/systemd/yuki-api.service.example` after creating the dedicated
non-root `yuki-api` account and its writable state/log directories. Keep code
and virtualenv root-owned and read-only. Logs go to journald. The API and
worker run as separate service identities and should receive separate
least-privilege database roles and secret scopes, even when they share the
same PostgreSQL cluster and policy/audit trust domain.

## Lifecycle and readiness

The existing `ConversationRuntime` ASGI lifespan owns application shutdown
and then closes its injected worker (if present) and security runtime in the
defined order. Keep the API and worker as separate processes: do not inject a
worker runtime into the API service. Use `GET /health/ready` for the
production audit-integrity readiness signal; it returns a generic failure
when durable security readiness cannot be established. `GET /healthz` is
only a process liveness signal and is not proof of production readiness.

## Scope and rollout gate

This is a Linux API process entry point, not a completed deployment. It does
not provision OCI, create identity/policy/anchor services, run migrations,
configure TLS, publish checkpoints, or deploy itself. Before private staging,
complete the deployment bootstrap, dedicated database roles, migration
executor, Vault integration, reverse proxy, monitoring, backup/restore drill,
release verification, and rollback plan. Start on a private OCI network only.
Windows service packaging remains a later acceptance target.
