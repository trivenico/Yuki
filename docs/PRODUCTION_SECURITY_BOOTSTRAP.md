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
- Call ProductionSecurityContext.verify_audit_integrity() from readiness checks and after recovery/restore; any missing, unavailable, mismatched, or stale anchor must keep the service unready. The default maximum checkpoint age is 300 seconds and can be changed only by trusted host bootstrap using audit_checkpoint_max_age_seconds.
- Run AuthorizationAuditCheckpointPublisher(audit_log, anchor).run_once() from an externally managed scheduler. It verifies the existing checkpoint, refreshes it even when the event head is unchanged, advances it when new events exist, and refuses missing anchors or local history behind the anchor. Alert on publisher failures and publication lag; never roll an external checkpoint backward.
- Provision the initial zero checkpoint through a separate operator-controlled bootstrap procedure after verifying the empty local audit database. The publisher intentionally refuses to initialize an absent anchor automatically.
- Resolve human identities from authenticated sessions and workload identities from cryptographically verified, audience-bound, short-lived credentials plus current task leases.
- Keep provider credentials in secret storage; never place them in the security context or audit events.
- Run targeted tests and the full CI suite before rollout.
- Verify that startup fails when the security context, policy backend, identity resolver, or audit writer is missing.

## Audit checkpoint integration

Strict production composition requires an object implementing AuthorizationAuditAnchor. The context validates the anchor against the local audit database during construction and exposes verify_audit_integrity() for repeated readiness checks. The stock web launcher still does not construct the external anchor service; deployment-owned bootstrap must inject it alongside the gateway. Checkpoint publication remains an operational responsibility of a host scheduler until a managed scheduler/exporter is integrated. A test-only in-memory anchor is not suitable for production.


## WI-5X checkpoint operations

The publisher is a one-shot operation, not an in-process timer. Deploy it as a separately supervised scheduled job with exclusive permission to publish checkpoints. Configure the schedule comfortably inside the production freshness budget (default 300 seconds), alert before the budget is exhausted, and expose the last successful checkpoint timestamp/event ID as operational telemetry. The production context rejects checkpoints older than its configured freshness window and checkpoints more than 30 seconds in the future. A successful publisher run refreshes the publication timestamp even if no new authorization event was appended.

The external anchor implementation must provide durable, monotonic compare-and-set semantics for event IDs and must be administered separately from the Yuki host. The reference publisher checks the returned value after publishing, but it cannot manufacture independence, remote durability, IAM isolation, or an atomic compare-and-set contract for a host adapter. Do not use the in-memory test anchor in production.


## Production readiness endpoint

Strict centrally authorized API runtimes expose GET /health/ready. The endpoint re-verifies the durable local authorization audit chain against the independently administered checkpoint, including the configured checkpoint freshness budget. It returns HTTP 200 only when that audit check succeeds; unavailable, stale, or mismatched checkpoints return HTTP 503 with a generic failure indicator and no internal exception details. Responses use no-store caching.

The endpoint is an audit-integrity readiness signal, not a complete application health certification. Deployment orchestration should combine it with dependency, policy-engine, identity-provider, and provider availability checks. Poll it at a cadence shorter than the checkpoint freshness budget. Local/legacy runtimes do not expose this production-only readiness route.


## WI-6A HTTPS anchor client

Host bootstrap may use HttpsAuthorizationAuditAnchor from core.authorization_audit as the concrete client adapter. Supply a verified ssl.SSLContext; for mutual TLS, load the dedicated client certificate/key into that context from the deployment's secret manager. Supply a short-lived bearer-token provider backed by the independently managed anchor service identity. Never place credentials in the endpoint URL or pass them through request/task data.

The remote endpoint must implement the atomic ETag contract documented in docs/WI_6A_HTTPS_AUDIT_ANCHOR_PROVIDER.md. It must stamp the checkpoint timestamp, enforce monotonic event ID/hash history, and persist the record in a separate trust domain. The client rejects redirects, TLS verification downgrades, malformed receipts, stale ETag conflicts, and unconfirmed writes.

This is a client adapter only. It does not deploy or operate the remote service, provision client certificates, issue bearer credentials, define cloud IAM, or create immutable remote storage. Production must not treat an arbitrary HTTPS endpoint as a trustworthy anchor merely because TLS succeeds.


## WI-6B independent anchor service

Yuki includes a self-hostable ASGI checkpoint service in core/audit_anchor_service.py. Deploy AuditAnchorASGI and SQLiteAuditAnchorService in a separate trust domain from the Yuki application and its authorization audit database. Use a dedicated database path on a local filesystem with reliable SQLite locking. This is a service implementation, not a hosted deployment or a high-availability/multi-region storage solution.

The ASGI app requires a host-injected authenticate_scope callback that returns the verified publisher identity from the TLS/workload authentication layer. It must not trust identity fields in request JSON or arbitrary forwarding headers. Put the app behind TLS with mandatory client-certificate verification or an equivalent workload identity gateway. If the WI-6A bearer token is also used, validate it in the trusted host middleware; the core service does not persist bearer secrets.

Publisher enrollment and revocation use SQLiteAuditAnchorService.set_publisher and require a host-injected administrator authorizer. These operations are recorded in the anchor service's administrative audit table. Keep administrator credentials and publisher credential lifecycle outside Yuki and outside the checkpoint database.

The service atomically enforces strong ETag compare-and-set, create-only initial publication, increasing event IDs, immutable same-ID hashes, and server-side publication timestamps. It cannot independently reconstruct all intermediate Yuki audit events from a head checkpoint. The publisher remains responsible for verifying its local chain before publication.

See docs/WI_6B_INDEPENDENT_AUDIT_ANCHOR_SERVICE.md for the complete deployment boundary and remaining operational requirements. Do not claim production independence until the service is deployed separately, identity middleware and administrator controls are configured, and backup/restore plus revocation drills are completed.


## WI-6C audit-anchor workload identity

Use `AuditAnchorCredentialAuthenticator` from `core.audit_anchor_identity` as the trusted `authenticate_scope` adapter for `AuditAnchorASGI`. Configure a unique exact audience for the anchor deployment and inject the existing trusted public-key resolver plus a shared atomic credential-state backend. The dedicated `yuki-audit-anchor+jwt` profile is separate from task/lease-bound agent credentials because an independent anchor publisher is not a task worker. Credentials are Ed25519-signed, one-use, audience-bound, scope-limited, and limited to five minutes. GET requires `audit-anchor:read`; PUT requires `audit-anchor:publish`.

The resolver controls key rotation: distribute a new public key before switching the signer, retain old keys only during a bounded overlap, then retire the old key in the resolver. The credential-state backend must provide durable atomic replay prevention and revocation across service instances; backend failure rejects authentication. The anchor publisher allowlist remains an additional authorization check after credential verification.

Keep publisher issuance and administrator operations separate. This PR does not implement a production credential issuer, admin HTTP API, KMS/HSM provisioning, or hosted deployment. The `audit-anchor:publishers:admin` scope is reserved and is not an implemented admin endpoint. Continue to authenticate administrators through the separately controlled host callback and expose enrollment/revocation only through an independently authenticated administrative control plane. See docs/WI_6C_AUDIT_ANCHOR_WORKLOAD_IDENTITY.md.


## WI-6D audit-anchor issuance and administration

Use `AuditAnchorCredentialIssuer` to issue WI-6C publisher credentials only after an injected trusted bootstrap identity source authenticates the workload and the anchor service confirms that identity is currently enrolled. The issuer accepts only the two publisher scopes, creates a fresh one-use token ID, binds the exact anchor audience, and limits credential lifetime to five minutes. Keep the signer backed by non-exportable KMS/HSM key custody.

Mount `AuditAnchorAdminASGI` separately from the publisher API. Its administrator authenticator is independent of publisher credentials, and the service's administrator authorization callback provides a second authorization check. Protect the admin listener with separate network controls and host identity policy. Use the admin routes only for enrollment, revocation, and administrative audit reads; the reserved administrator token scope is not a substitute for this control plane.

Forward administrative audit records to independently controlled storage/monitoring. The local SQLite admin audit table is transactional but not tamper-proof. Production still requires deployment-owned bootstrap authentication, KMS/HSM, shared durable replay/revocation state, and separate listener/network enforcement. See docs/WI_6D_AUDIT_ANCHOR_ISSUER_ADMIN_CONTROL_PLANE.md.


## WI-6E audit-anchor operations and recovery

Mount `AuditAnchorReadinessASGI` on the internal health listener for `GET /health/live` and `GET /health/ready`. Readiness checks SQLite integrity, required schema, checkpoint shape, and private regular-file permissions; it returns only generic status. It does not certify KMS, identity, replay-state, network, or remote audit publisher health. Gate public traffic on those additional deployment checks and restrict health endpoints to trusted monitoring networks.

Schedule `create_audit_anchor_backup(service, destination_path)` to create a consistent online SQLite backup. The helper uses SQLite's backup API, validates the resulting database, fsyncs and atomically replaces the destination, and returns a SHA-256 digest and checkpoint event ID. Export backups to a separately controlled failure domain with encryption, retention, and immutable/versioned storage; the helper does not upload or make backups immutable.

Perform restore drills: restore to a new path, verify the digest and SQLite integrity, reconcile the stored checkpoint with the independent audit chain and external anchor, and never roll back the external checkpoint to accommodate an older backup. SQLite remains a single-node/local-filesystem service. See docs/WI_6E_AUDIT_ANCHOR_OPERATIONS_RECOVERY.md.


## WI-6F durable audit-anchor credential replay state

Use `SQLiteAuditAnchorCredentialState` from `core.audit_anchor_credential_state` as the `credential_state` dependency for `AuditAnchorCredentialAuthenticator` when all anchor authenticator processes run on one host with a local SQLite filesystem. It provides atomic consume-once and persistent revocation state across local processes. Keep its database separate from the checkpoint and Yuki audit databases. Do not treat SQLite as a cross-host or multi-region coordination backend; use a managed transactional replay/revocation service implementing `WorkloadCredentialState` for multi-host deployments.

A restored older replay-state backup may re-enable credentials consumed after that backup. Reconcile revocation events and maximum token lifetime before reopening publisher traffic; when uncertain, retire affected signing keys and wait out the five-minute maximum token lifetime. See docs/WI_6F_DURABLE_AUDIT_ANCHOR_CREDENTIAL_STATE.md for deployment and recovery drills.


## Production readiness probe

`core.production_security_readiness.ProductionSecurityReadinessASGI` exposes a minimal health application for a deployment-owned internal health listener:

- `GET /health/live` reports process-level liveness only. It deliberately does not depend on the external audit anchor, so an audit outage does not cause an orchestrator restart loop.
- `GET /health/ready` calls `ProductionSecurityContext.verify_audit_integrity()` on every request. A missing, stale, unavailable, or mismatched checkpoint returns a generic 503 response; no exception details or checkpoint contents are exposed.
- Other paths return 404 and non-GET methods return 405. Responses use no-store/no-cache and nosniff headers.

Mount this app only on a private health interface or an independently access-controlled internal listener. It is a separate ASGI application, not an automatic route injection into the public chat app. The deployment must wire it to the exact same validated `ProductionSecurityContext` used by the production runtime and must ensure the health listener is not exposed as an administrative or public application surface.

The readiness probe validates the authorization audit chain and its external checkpoint freshness. It does not prove database backup recoverability, KMS availability, publisher ingress isolation, TLS termination, policy service health, or general application health; compose those checks in deployment orchestration. Liveness and readiness must remain distinct.
