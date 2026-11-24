# Production web security bootstrap

The web launcher has two modes:

- **Local development:** loopback binding with no production security context retains the existing deny-all-tools development composition.
- **Production/public:** setting YUKI_ENVIRONMENT=production, setting YUKI_WEB_ALLOW_PUBLIC_BIND=true, or selecting a non-loopback YUKI_WEB_HOST activates strict startup. The launcher refuses to load application configuration or initialize providers unless its host calls create_app(production_security=...) with a trusted ProductionSecurityContext.

The production context must be assembled by deployment-owned bootstrap code. Its actor resolver must derive the authenticated human principal from the server's trusted identity/session system. It must not accept a principal ID, tenant, agent, permissions, or policy revision from environment variables, YAML, prompts, model output, or request bodies. Its authorization gateway must use the production policy engine and a durable, monitored audit writer plus an independently controlled audit checkpoint anchor. The policy revision must be obtained from the trusted policy deployment.

In strict mode, scripts.run_web.create_app creates an owner-scoped AgentExecutionLoop for each authenticated owner. Each loop uses an AuthorizedProviderAgentAdapter and a GuardedToolRuntime bound to the exact shared gateway, resolved principal, and policy revision. Streaming and non-streaming inference therefore pass through the provider authorization boundary. Provider switching remains separately authorized as inference:provider:select.

## Current operational limitation

The stock python -m scripts.run_web entry point does not yet load or construct a production identity resolver, policy engine, or durable audit writer. It therefore fails closed for production/public mode rather than fabricating trust from configuration. A deployment must provide a trusted host integration that imports create_app and supplies the context. Do not work around this gate by setting a local/development mode on an internet-facing listener.

The main.py guarded terminal runtime and Docker image are separate local-first surfaces; this web bootstrap gate does not certify them as a production service. Their strict CLI authorization mode and worker bootstrap remain separate deployment responsibilities.

## Required deployment checks

- Use TLS termination and a trusted reverse proxy; restrict forwarded headers and hostnames.
- Supply a policy engine with a pinned/validated policy revision.
- Supply a durable audit writer and independently controlled checkpoint anchor; ProductionSecurityContext verifies the local chain against the external checkpoint before the context can be constructed.
- Call ProductionSecurityContext.verify_audit_integrity() from readiness checks and after recovery/restore; any missing, unavailable, or mismatched anchor must keep the service unready.
- Publish checkpoints on a monitored schedule through SQLiteAuthorizationAuditLog.publish_checkpoint(anchor); alert on publication lag and never roll an external checkpoint backward.
- Resolve human identities from authenticated sessions and workload identities from cryptographically verified, audience-bound, short-lived credentials plus current task leases.
- Keep provider credentials in secret storage; never place them in the security context or audit events.
- Run targeted tests and the full CI suite before rollout.
- Verify that startup fails when the security context, policy backend, identity resolver, or audit writer is missing.

## Audit checkpoint integration

Strict production composition requires an object implementing AuthorizationAuditAnchor. The context validates the anchor against the local audit database during construction and exposes verify_audit_integrity() for repeated readiness checks. The stock web launcher still does not construct the external anchor service; deployment-owned bootstrap must inject it alongside the gateway. Checkpoint publication remains an operational responsibility of a host scheduler until a managed scheduler/exporter is integrated. A test-only in-memory anchor is not suitable for production.

## Scheduled audit maintenance

The deployment scheduler should invoke `production_security.audit_maintenance().run_once()` on a monitored interval. The bounded job verifies the local chain and current external checkpoint before publishing, publishes only a newer head, then verifies the result. Exceptions must fail the scheduled job and trigger alerting; do not mark the cycle healthy when the anchor is unavailable. Anchor adapters must durably enforce monotonic event IDs, reject conflicting hashes for an existing event ID, and make identical checkpoint publication idempotent. Initial anchor provisioning remains a separate operator-controlled step.
