# Linux-first Yuki pilot and Windows follow-on

Status: **engineering plan; no Linux VPS, service, credentials, or deployment has been provisioned.**

## Platform decision

The first hosted Yuki pilot targets Linux on Oracle Cloud Infrastructure (OCI). Windows is a later supported deployment target, after the Linux runtime has passed its operational and security acceptance gates. Keep core Python application logic, provider contracts, data formats, and policy behavior platform-neutral; isolate operating-system integration behind deployment adapters.

This is a sequencing decision, not a decision to remove Windows support. Existing Windows launchers and desktop compatibility must remain covered by CI while Linux receives the first production-shaped deployment.

## What runs on Linux first

The repository currently exposes several distinct execution surfaces:

- `yuki` CLI: diagnostic, setup, provider, one-shot ask, and interactive persistent-chat commands.
- `main.py`: guarded interactive terminal runtime with approval prompts and local-only security composition.
- `scripts.run_web`: ASGI web application. Strict production/public mode requires an injected deployment-owned `ProductionSecurityRuntime`; the stock launcher intentionally refuses to fabricate that trust boundary.
- Dockerfile: a convenience image for the guarded terminal runtime, not a certified production web service or a complete security sandbox.

**Do not install the interactive CLI or `main.py` as an always-on systemd daemon.** They are not daemon lifecycle entry points. Do not expose the stock web launcher publicly or weaken its strict startup checks to make a pilot appear live.

Before creating a systemd service, implement and review a dedicated Linux service composition entry point with:
- explicit configuration and dependency injection;
- signal-aware startup, readiness, graceful shutdown, and bounded shutdown timeout;
- deployment-owned identity/session resolution and policy;
- shared PostgreSQL production security runtime and applied migrations;
- durable authorization audit writer and separately administered independent checkpoint anchor;
- provider initialization with bounded timeouts and explicit routing;
- persistent state paths outside the immutable release directory;
- private health/readiness and operator telemetry surfaces;
- deterministic failure and cleanup when any required dependency is unavailable.

The service must remain unready and protected operations must fail closed if any required security dependency is absent. A local SQLite audit log or process-local checkpoint is not a production substitute.

## Linux host baseline

For the first private OCI pilot:

- Use a supported Linux image and a dedicated, non-login service account.
- Keep source/release files immutable and owned by deployment; keep mutable state under a dedicated persistent data directory.
- Store secrets in OCI Vault or a root-controlled runtime injection mechanism. Never commit secrets, bake them into images, or expose them in process arguments or logs.
- Run with no ambient Linux capabilities, no privilege escalation, a restrictive systemd sandbox, private temporary storage, protected home/kernel/control groups, and only the filesystem write paths the service needs.
- Bind the application to loopback or a private service network behind a separately configured TLS reverse proxy. The initial pilot has no public ingress.
- Use default-deny network rules, OCI Bastion/private VPN administration, least-privilege instance identity, encrypted volumes, time synchronization, monitoring, budget alerts, and tested backups.
- Separate liveness from readiness. Readiness includes security runtime, PostgreSQL, policy, identity, audit-chain and independent-anchor checks; do not leak internal errors to unauthenticated callers.
- Send structured operational logs to journald/approved monitoring with secrets and sensitive conversation content redacted.
- Pin every deployment to an immutable release/artifact digest and source commit.

## Controlled Linux release lifecycle

1. CI validates a pull request; no pull request job receives deployment credentials.
2. A protected release workflow creates an immutable, provenance-bearing artifact.
3. An explicitly approved private staging deployment verifies artifact digest/provenance and stages a new version alongside the active version.
4. A deployment-owned migration step runs the reviewed ordered migration manifest under exclusive locking; application replicas do not race to migrate.
5. The deployment switches the active version, restarts the service, and checks private readiness plus smoke tests.
6. On failure, restore the prior code version and configuration. Database rollback is a separate compatibility-aware operation; never assume switching code backward reverses a committed migration.
7. Record release ID, source commit, artifact digest, configuration revision, migration revision, model revision, operator approval, health result, and rollback outcome.
8. Rehearse backup restore and rollback before real pilot use.

The CLI wheel workflow is not a production service package. Do not claim OCI deployment readiness until the daemon composition, deployment adapter, migrations, and operational checks above exist and have been exercised in a disposable environment.

## Training and runtime separation

Linux runtime is the serving plane, not a training sandbox. Kaggle experiments remain isolated from OCI production credentials, live databases, and private user conversations. Hugging Face datasets and model candidates are pinned to immutable revisions and digests. Candidate promotion requires provenance, license/privacy/security checks, the three independent evaluation tracks (assistant quality, coding/engineering, agent/tool-use reliability), and human approval. The running VPS keeps its currently approved model until an explicit promotion; rollback retains the previous approved revision.

## Linux pilot exit criteria

- Clean installation from a pinned artifact on the selected OCI Linux image.
- Dedicated service account and systemd hardening reviewed against actual writable paths and required address families.
- Service starts, becomes ready only with all production security dependencies available, and fails closed when each dependency is independently removed.
- Authentication, authorization, audit append, independent checkpoint verification, key revocation, and policy outage behavior tested.
- PostgreSQL migrations run once in the deployment phase; application startup never silently migrates or falls back to SQLite.
- Restart, SIGTERM, process crash, host reboot, provider outage, PostgreSQL outage, disk pressure, backup, restore, and rollback rehearsed.
- Private ingress, TLS proxy trust, firewall, secret rotation, monitoring, budget alerts, and operator access verified.
- CI and security checks green; an operator signs off on pilot evidence.

## Windows follow-on

After Linux pilot exit criteria are met, add a Windows deployment adapter rather than forking application behavior:

- Windows service lifecycle and service-account permissions;
- DPAPI/Windows Credential Manager or an approved secret broker;
- ACLs and protected persistent-data directories;
- Windows firewall and private reverse-proxy integration;
- event-log/metrics integration, graceful stop, crash recovery, update staging, and rollback;
- clean-install, upgrade, restore, and security regression tests on supported Windows versions.

Keep Windows CI active throughout Linux development. Windows release approval is separate; Linux pilot success alone does not certify Windows production readiness.

## Current blockers and explicit non-actions

- OCI region, tenancy, compartment, shape, network CIDRs, and spend alert remain owner decisions.
- Production web host bootstrap and service lifecycle entry point are not yet implemented.
- The production identity/policy services, PostgreSQL instance/migrations, durable audit anchor service, Vault integration, monitoring, and backups are not provisioned.
- No OCI infrastructure apply, public exposure, Kaggle training run, Hugging Face upload, model promotion, merge, or deployment is authorized by this plan.
