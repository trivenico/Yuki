# Yuki remote access and 24/7 deployment architecture

## Goal

Allow the owner to use Yuki from a phone or computer while the control plane runs continuously. Remote reachability must not imply unrestricted access to the host or to personal devices.

## Recommended first deployment: private cloud control plane + optional laptop worker

1. **Oracle Cloud VM**: run the API/control plane, durable workflow database, queue/worker supervisor, health checks, and alerting. Pin a supported OS image and patch regularly.
2. **Private access first**: expose the UI/API through a private VPN such as WireGuard, or a managed identity-aware tunnel such as Cloudflare Tunnel with Access. Do not publish the raw application port to the public internet.
3. **Strong identity**: require HTTPS, MFA/identity-provider protection at the access edge, application-level authentication, short-lived sessions, rate limits, CSRF protection for browser sessions, and audit events for privileged actions.
4. **Laptop worker**: connect outbound to the cloud control plane over authenticated TLS. Give the worker a dedicated identity and a narrow task allowlist. Do not expose the laptop's inbound ports or mount its whole filesystem into cloud services.
5. **Approval boundaries**: require fresh owner approval for consequential actions (file writes/deletes, messaging, purchases, device controls, deployments, security changes). Read-only work may use a separate low-risk policy.
6. **Secrets**: store provider keys in a secret manager or protected environment injection; never commit them, put them in workflow payloads, or include them in logs.
7. **Durability**: back up the database and required artifacts with encryption and tested restore procedures. Monitor disk space, queue age, worker heartbeats, provider failures, and backup freshness.
8. **Network policy**: restrict cloud ingress to the private access path and required administration source. Use outbound egress restrictions where practical. Keep OS firewall enabled.
9. **Recovery**: restart services under a supervisor; use health/readiness checks and bounded retries. Never automatically replay a side-effecting task unless it has an idempotency guarantee and policy permits it.
10. **Privacy**: classify data before cloud processing. Keep sensitive files and local-only model work on the laptop when possible.

## Deployment choices

- **Cloud-only**: easiest always-on access, but local files/devices and GPU-heavy local models are not automatically available.
- **Laptop-only**: keeps data local, but availability depends on power, sleep settings, and home internet.
- **Hybrid (recommended)**: cloud coordinates and stores durable task state; laptop performs explicitly delegated local work. The cloud must treat the laptop as an intermittently connected worker.

## Rollout gates

- Start with a private, read-only interface and a non-sensitive test account.
- Verify authentication, authorization, session expiry/revocation, CSRF protections, audit logs, rate limiting, and dependency updates.
- Test restart, network loss, worker disconnect, database restore, disk full, expired credentials, and cancellation during a multi-step task.
- Run an external security review before any public-facing deployment.
- Add public internet access only after threat modeling and penetration testing; private VPN/tunnel is the default.

## Current status

This is an architecture and rollout checklist, not evidence that Oracle Cloud resources have been created or Yuki has been deployed. No VM, DNS name, tunnel, firewall, secret store, or production credentials are configured by this document.
