# Yuki first pilot execution plan

Status: **execution plan; no infrastructure has been provisioned**.

## Decision record

- Pilot targets: **both VPS and local hardware**.
- First environment: **private VPS staging**, because VPS provisioning is in progress.
- Hardware timing: **deferred** until a suitable device is available; retain the same application release and acceptance suite for the later hardware run.
- Hosting provider, Linux distribution/version, region, instance size, storage class, access network, model provider, and credential source: **not selected in this plan**. Record them before provisioning; do not infer them from this document.
- Initial pilot data: synthetic/non-sensitive only.
- Exposure: private SSH tunnel or private VPN; no public listener, public reverse proxy, or public DNS route.

This plan does not authorize infrastructure purchases, account creation, secret issuance, deployment, or public exposure.

## Execution order

### Workstream A — engineering readiness (start now)

1. **Pilot preflight command**
   - Extend the existing `scripts/doctor.py` rather than creating a competing diagnostics entry point.
   - Report OS/platform, architecture, Python version, package/import health, writable data root, available disk space, and process identity.
   - Check that configured data/log/backup paths are outside the Git checkout where required and have restrictive permissions on POSIX.
   - Inspect effective web bind configuration and fail the VPS profile if it is not loopback-only.
   - Report whether provider configuration is present without printing values, tokens, URLs containing credentials, or exception text.
   - Report backup destination and whether a recent backup exists; do not claim restore readiness without a restore verification record.
   - Separate hard blockers from optional capabilities. Diagnostics must be read-only and must not create credentials, alter firewall rules, contact external services, or mutate databases.
   - Add deterministic unit tests for each check and redaction behavior. A doctor PASS means only that local preconditions look plausible; it is not a security certification.

2. **Repeatable pilot configuration**
   - Define a documented, non-secret environment template and a single explicit data-root convention.
   - Keep provider keys and session credentials in a secret manager or protected environment injection; never in YAML, source control, cloud-init output, shell history, or diagnostics.
   - Pin the tested Python/runtime dependency set and record the exact Git commit and installation method.
   - Do not add an unreviewed container or systemd deployment path merely for convenience. Review service sandboxing, filesystem access, environment handling, shutdown, and upgrade semantics before adopting one.

3. **Acceptance harness**
   - Define smoke tests for startup/shutdown, one real provider round-trip, timeout/error handling, streaming/cancellation where supported, local memory, task proposal/approval, restart recovery, and log redaction.
   - Use synthetic prompts and a disposable workspace.
   - Capture cold start, first-token/response latency, CPU/RAM, disk growth, and provider usage.
   - Keep active network testing, arbitrary shell execution, self-modifying behavior, and unattended actions disabled.

4. **Release and rollback discipline**
   - Select a tested commit only after relevant GitHub Actions checks are green.
   - Record commit SHA, dependency lock/hash evidence, configuration version, and pilot operator.
   - Keep the previous known-good revision and a verified data backup.
   - Upgrade only during a controlled maintenance window; stop on failed preflight, failed backup, failed health checks, or unexpected schema changes.
   - Rollback must restore the application revision and a compatible data snapshot. Never roll back an external audit checkpoint to accommodate an older local backup.

### Workstream B — VPS provisioning (once the provider is selected)

Before creating the instance, record:
- Provider/account owner and billing alert limits.
- Region and data-residency requirements.
- Supported Linux distribution and version.
- CPU, RAM, disk capacity/type, backup/snapshot capability.
- Administrator source IP or private VPN plan.
- SSH key owner and recovery procedure.
- DNS/TLS decision (not needed for the loopback-only pilot).
- Secret injection mechanism and provider/model budget cap.

Provisioning baseline:
- Dedicated disposable instance; dedicated non-root service account.
- SSH keys only; disable password SSH where supported.
- Firewall default-deny; permit SSH only from the administrator-controlled source or private VPN.
- No public Yuki, database, metrics, debug, or admin listener.
- Bind Yuki to loopback; access only by SSH tunnel or private VPN.
- Security updates enabled; time synchronization enabled.
- Restrictive ownership and permissions for data, logs, and secrets.
- Explicit CPU, memory, process, and disk limits; log rotation and disk alerts.
- Encrypted storage and provider snapshots/backups where available.
- No production or personal data in the first pilot.

Do not set `YUKI_ENVIRONMENT=production`, `YUKI_WEB_ALLOW_PUBLIC_BIND=true`, or a non-loopback host to make the current stock launcher start. Strict production mode requires a deployment-owned production security runtime; the stock launcher intentionally fails closed without it.

### Workstream C — private VPS acceptance

The pilot is admitted only when all of these are evidenced:

| Gate | Required evidence |
|---|---|
| Provenance | Exact commit SHA and successful required CI checks |
| Host | OS, architecture, Python/runtime, non-root service identity |
| Network | Listener bound to loopback; external port scan from an authorized test point confirms no Yuki listener is exposed |
| Secrets | No credentials in repository, process arguments, logs, cloud-init output, or diagnostics |
| Storage | Dedicated data path, restrictive permissions, disk limits/alerts |
| Runtime | Startup, chat/provider round-trip, timeout, cancellation, graceful shutdown |
| Recovery | Reboot and process-crash recovery; backup restored to a separate disposable path |
| Operations | Log rotation, resource observations, provider budget/rate limits, operator runbook |
| Rollback | Previous revision and compatible backup restored in a rehearsed drill |

A private pilot is not a public production release, multi-tenant service, or proof of production security.

### Workstream D — local hardware pilot (later)

When hardware becomes available:
- Reuse the same release commit, synthetic test workspace, and acceptance suite.
- Record device model, OS/version, CPU architecture, RAM, available accelerator, storage, and peripherals.
- Validate clean installation, provider/local-model setup, startup, latency, resource usage, audio/voice only if explicitly enabled, persistence, backup/restore, and shutdown.
- Keep hardware-specific results separate from VPS results; do not infer one environment's reliability from the other.
- Test portable SSD behavior only with a separate verified backup; never make the SSD the sole copy of pilot data.

## Production boundary and blockers

The first VPS target is private staging only. Public production remains blocked until all of the following are implemented and independently verified:

1. Deployment-owned trusted human identity/session resolution and policy engine with pinned policy revision.
2. PostgreSQL-backed production security runtime and durable authorization audit writer.
3. Independently administered audit checkpoint anchor, including identity/key custody, replay/revocation state, backup/restore, and recovery drills.
4. A single ordered production migration executor with durable ledger, checksums, advisory locking, preflight, and failure semantics.
5. TLS/reverse proxy, trusted host/proxy policy, rate limits, secret management, monitoring, alerting, retention, and incident response.
6. Fail-closed readiness and end-to-end authorization/audit acceptance on the actual release artifact.

SQLite may remain the supported personal-MVP persistence backend if its release acceptance gates pass. Do not force a PostgreSQL cutover merely to run the private pilot.

## Exit criteria and handoff

The VPS pilot is complete only when the acceptance table is filled with evidence, defects are triaged, backup restore and rollback are rehearsed, and the operator signs off on the exact commit. The hardware pilot follows later and produces its own evidence record.

At provisioning handoff, supply the selected provider, OS/version, instance shape, region, private access method, and secret-injection method. Do not send passwords, private keys, API tokens, or recovery codes in chat or commit them to GitHub.
