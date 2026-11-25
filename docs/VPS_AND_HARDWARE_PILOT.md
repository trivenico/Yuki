# Yuki VPS and real-hardware pilot gates

Status: **pilot plan; no infrastructure has been provisioned**. This runbook separates early hands-on feedback from a public production deployment.

## The short answer

- **Real hardware:** a restricted local developer-preview session can begin as soon as the target machine has the documented Python/runtime dependencies and a backed-up, non-sensitive test workspace. It is useful for CLI, provider, latency, memory, and usability feedback; it is not production acceptance.
- **VPS:** a private staging pilot is the next infrastructure milestone. A reasonable planning target is **1–2 weeks of focused engineering** to prepare a repeatable, restricted pilot, assuming the chosen VPS, OS, access method, and provider credentials are available. This is an estimate, not a committed date.
- **Public production VPS:** not yet. The stock web launcher intentionally fails closed in strict/public mode unless a deployment-owned production security runtime is injected. Identity, policy, durable audit, independent audit anchor, TLS/reverse proxy, monitoring, backup/restore, and deployment automation must be supplied and verified first.

Do not expose the current developer preview directly to the internet to accelerate feedback.

## Pilot stages

### Stage 0 — local hardware smoke test (can start first)

Use one developer-owned machine and a fresh test workspace.

- Record OS/version, CPU architecture, RAM, disk, Python version, and available accelerator.
- Install from the repository's documented setup path; capture every manual step.
- Configure one supported provider using a secret store or protected environment; never commit credentials or put them in logs.
- Use synthetic/non-sensitive prompts and test data.
- Exercise startup/shutdown, basic chat, provider errors/timeouts, streaming, cancellation, local memory, task proposals, approvals, and restart recovery where supported.
- Measure cold start, first-token latency, response latency, memory/CPU, and disk growth.
- Back up the workspace, restore it to a separate test path, and verify the restored data.
- Record defects with reproduction steps, logs with secrets removed, machine details, and expected/actual behavior.

This stage validates the developer-preview experience only. Do not enable unrestricted shell execution, self-modifying agents, active network testing, or unattended operation.

### Stage 1 — private VPS staging pilot

Use a disposable VPS with a supported Linux distribution, non-root service account, SSH key authentication, firewall default-deny, automatic security updates, and encrypted storage/volume where available.

Network boundary:
- Bind Yuki to loopback only and reach it through an SSH tunnel or a private VPN.
- Do not set public-bind flags or point a public reverse proxy at the current stock launcher.
- Restrict SSH ingress to an administrator-controlled source range or private access network.
- Keep database, metrics, debug endpoints, and admin surfaces off the public interface.
- Do not place secrets in cloud-init output, shell history, source control, or deployment logs.

Data and operations:
- Use a dedicated non-production account and synthetic data.
- Set explicit CPU, memory, disk, and process limits; configure log rotation and disk alerts.
- Use a dedicated persistent data directory with restrictive ownership/permissions.
- Define backup cadence and retention; test restore onto a second disposable instance.
- Test reboot, process crash, disk-pressure behavior, graceful shutdown, and recovery.
- Keep a written rollback path: stop service, preserve diagnostic bundle, restore known-good application revision and data snapshot.
- Record provider usage/cost limits and rate limits before extended use.

Do not call this production, multi-tenant, or internet-safe. This private pilot is for controlled learning and reliability fixes.

### Stage 2 — production-shaped security staging

Only after the private pilot is stable:
- Build a deployment-owned host bootstrap that injects trusted human identity/session resolution, policy engine and pinned policy revision, PostgreSQL-backed security runtime, durable audit writer, and independently administered audit checkpoint anchor.
- Provision the independent audit-anchor service in a separate trust domain with its own identity, key custody, replay/revocation state, backups, and admin controls.
- Implement and rehearse the production migration executor described in `POSTGRES_MIGRATION_OPERATIONS.md`; run it as a dedicated release job, never from every web worker.
- Add TLS termination, trusted proxy/host policy, rate limits, secrets management, observability, alerting, retention, and incident response.
- Verify readiness fails closed when identity, policy, audit, database, or anchor dependencies are unavailable.
- Complete threat-model review, restore drill, credential rotation/revocation drill, and security review before any public exposure.

### Stage 3 — real-user production acceptance

- Run the exact release commit and deployment artifact on the target VPS/hardware.
- Execute Gate A acceptance tests with the actual provider and supported OS.
- Verify authentication, authorization, audit integrity, backup/restore, resource limits, upgrade/rollback, and monitoring.
- Keep autonomous code execution and active security testing disabled unless their separate isolation and authorization gates are independently passed.
- Start with a small, explicitly authorized pilot cohort and a rollback owner.

## Minimum acceptance record

For every pilot run, capture:

| Area | Evidence required |
|---|---|
| Installation | Clean install transcript, versions, reproducible steps |
| Reliability | Startup/shutdown, crash/restart, cancellation, recovery results |
| Performance | Cold start, latency, CPU/RAM/disk under representative load |
| Data safety | Permissions, backup artifact, successful restore verification |
| Security | Listener binding, firewall rules, secret handling, auth boundaries |
| Provider | Model ID, timeout/error behavior, usage limits; never credentials |
| UX | Task success/failure examples, confusing flows, user feedback |
| Release | Exact Git commit, configuration profile, test results, known limitations |

## Exit criteria

A pilot can progress only when its evidence is reviewed and all high-severity defects are fixed. A successful private VPS pilot does not waive Gate A release checks or strict production security bootstrap. A successful local hardware smoke test does not prove cross-platform support; repeat the matrix for every OS/architecture we claim to support.

## Current status

No VPS, hardware device, cloud account, DNS, TLS certificate, production identity provider, external audit anchor, or database has been provisioned or accessed as part of this work. This document defines the controlled path; it is not evidence that a deployment exists.
