# Yuki Engineering Audit 062 — Deployment Architecture, CI/CD, and Production Operations

**Review date:** 2026-11-23  
**Repository:** `trivenico/Yuki`  
**Review type:** Targeted source, workflow, packaging, and operations-file inspection  
**Status:** Documentation audit; no deployment or release was performed.

> Scope note: This review inspected the GitHub Actions workflow inventory and selected quality, dependency, packaging, and worker workflows; the Dockerfile and its guidance; web launch configuration; backup scripts; readiness/diagnostic tests; and a systemd example. It did not inspect repository-host branch protection settings, GitHub environment secrets, cloud infrastructure, DNS, reverse proxy configuration, production logs, or deployed hosts. Workflow existence is not evidence that a job has passed on the current commit.

## Executive assessment

Yuki has a substantial engineering CI foundation and several useful operational safety features:
- Main quality workflow runs unit tests and compilation on Ubuntu, Windows, and macOS, with an installed-CLI matrix across Python 3.10–3.12.
- Dedicated workflows cover dependency manifest consistency, agent/runtime contracts, and selected capability domains.
- Desktop package workflow builds unsigned bundles on three operating systems, smoke-tests them, generates an SBOM and SHA-256 checksums, and attests provenance for main-branch artifacts.
- Docker runs as a non-root user and explicitly warns that the image is not a security sandbox.
- Web launcher defaults to loopback binding and requires an explicit opt-in for public binding behind a trusted TLS proxy.
- Backup scripts validate the archive before publishing it and apply a configurable retention window.
- A hardened systemd service example demonstrates useful Linux sandboxing controls.

However, the repository does not establish a complete production deployment or release pipeline. No production web/container deployment workflow, environment promotion process, signed release package publication, deployment rollback automation, or end-to-end monitoring/alerting configuration was found in the inspected inventory. Backup scripts are local archives, not a complete off-site encrypted disaster-recovery system. The README itself explicitly says Yuki is not production-ready.

## 1. CI workflow architecture

### `.github/workflows/tests.yml`

Observed:
- Runs on push, pull request, and manual dispatch.
- Sets workflow permissions to `contents: read`.
- Uses concurrency groups and cancels superseded runs.
- Runs compilation, dependency consistency, and unit tests on Ubuntu, Windows, and macOS using Python 3.11.
- Runs installed CLI/package checks across Python 3.10, 3.11, and 3.12 on all three operating systems.
- Runs a desktop Qt offscreen smoke test on Ubuntu.

Assessment: good baseline coverage for cross-platform compatibility and clean installation.

Gaps:
- The full unit suite is executed on Python 3.11 only; the other supported Python versions receive installed CLI smoke coverage rather than the complete suite.
- No explicit lint, formatting, static type, coverage threshold, or mutation-test gate appears in this workflow.
- No PostgreSQL service integration job is configured here; the persistence integration test is opt-in via `YUKI_TEST_POSTGRES_URL`.
- No explicit migration, backup/restore, deployment smoke, or rollback test gate is present in the inspected workflow.
- Test output is not shown as a retained coverage artifact or release evidence in this workflow.

Recommendations:
1. Run the complete fast test suite on every supported Python version at least on Linux.
2. Add lint, type-check, formatting, and coverage gates with documented thresholds.
3. Add service-backed PostgreSQL integration and migration jobs.
4. Add deterministic fault-injection and backup/restore CI jobs.
5. Make required checks explicit in branch protection/rulesets; verify settings in GitHub because they are not stored in this repository.

### Dedicated workflows

The workflow inventory includes focused checks for agent workers, durable workflows, provider capabilities, auth, task runtime, desktop, wakeword, and related domains.

Observed:
- Several specialized workflows are path-filtered and have manual dispatch.
- Most inspected workflows use read-only repository permissions.
- Focused workflows provide faster feedback for selected subsystems.

Risks and recommendations:
- Path filters can accidentally omit a workflow when a dependency or shared module changes. Keep the full quality workflow as the authoritative required PR gate.
- Add tests for workflow path filters and shared dependency changes.
- Standardize timeouts, permissions, concurrency, artifact retention, and Python setup across all workflows.
- Pin third-party actions to immutable commit SHAs for stronger supply-chain integrity; version tags such as `@v7` are mutable references.
- Consider GitHub Actions OIDC for cloud access rather than long-lived deployment credentials. Scope OIDC trust to repository, branch/tag, and environment.
- Never run untrusted pull-request code with privileged secrets or write permissions.

### Dependency automation

Observed:
- Dependabot weekly updates are configured for pip and GitHub Actions.
- A dedicated dependency audit checks duplicate/conflicting requirement declarations.

Important distinction: duplicate/conflict checking is not a vulnerability scan, malware assessment, license policy, or exploitability review.

Recommendations:
- Add dependency vulnerability scanning and a documented severity/SLA policy.
- Generate and retain SBOMs for all release artifacts, not only desktop bundles.
- Review transitive dependencies and licenses.
- Add lockfiles or constraints for reproducible builds, with a scheduled controlled update process.
- Pin build tooling and use hashes where operationally practical.

## 2. Docker image and runtime

### `docker/Dockerfile`

Observed:
- Uses `python:3.11-slim`.
- Installs runtime requirements.
- Creates UID 10001 user `yuki` and switches to it.
- Sets `/workspace` as the configured workspace and launches `python main.py`.

Positive controls:
- Non-root runtime user.
- Slim base image.
- No Docker socket or host credential mount is configured by default.
- `docker/README.md` explicitly says the image is not a sandbox and recommends dropping capabilities, read-only root filesystem, resource limits, and disposable workspace mounts.

Gaps:
- Base image is not pinned by digest.
- Python dependencies are range-constrained rather than locked to a reproducible environment.
- Build context copies the entire repository; no inspected Docker-specific ignore policy was confirmed.
- No explicit health check, image metadata/labels, or runtime resource policy is defined in the Dockerfile.
- No production web entrypoint or container deployment workflow is defined by this image; it launches `main.py`.
- No image scan, SBOM, signature, or registry promotion workflow was found in the inspected workflow inventory.

Recommendations:
1. Pin the base image by digest and update it through reviewed automation.
2. Use a controlled build context and a Docker-specific ignore file; exclude local data, caches, secrets, test artifacts, and developer files.
3. Install from a reproducible lock/constraints set and separate build-time from runtime dependencies.
4. Consider multi-stage build, non-root UID, read-only root filesystem, dropped capabilities, seccomp/AppArmor, and explicit CPU/memory/PID limits.
5. Set a health check only when it measures meaningful service readiness; do not use a process-exists check as proof that dependencies work.
6. Add container build, scan, SBOM, signature, provenance, and registry promotion gates.
7. Document supported container purpose, persistent volume layout, backup procedure, and upgrade/rollback behavior.

Do not treat Docker as a security boundary for hostile code. Use a dedicated hardened sandbox/worker architecture for untrusted execution.

## 3. Web service exposure and configuration

### `scripts/run_web.py`

Observed:
- Creates a web runtime with built-in authentication and owner-scoped persistence services.
- Starts Uvicorn with lifespan enabled.
- Defaults to `127.0.0.1`.
- Rejects public bind addresses unless `YUKI_WEB_ALLOW_PUBLIC_BIND=true`.
- Its module documentation requires TLS termination, trusted host/rate-limit policy, and secure secret provisioning for public deployments.
- Initial web profile exposes no tools through a deny-all tool authorizer.

Recommendations:
- Keep the application behind a maintained TLS reverse proxy or managed ingress.
- Configure trusted hosts, proxy headers, forwarded client IP handling, request size limits, timeouts, and rate limits.
- Ensure proxy trust is restricted to known proxy hops; do not trust arbitrary forwarded headers.
- Define readiness/liveness endpoints and ensure readiness checks required dependencies without exposing sensitive diagnostics.
- Use a process manager/container orchestrator with graceful shutdown, bounded workers, and restart policy.
- Ensure secrets are injected through a secret manager and never written to image layers or checked-in config.
- Verify bootstrap administrator credentials are provisioned once, rotated, and removed from the environment after bootstrap.
- Document the public deployment topology and security assumptions.

### Environment files

Observed:
- `.env.example` states the app does not auto-load `.env` and tells operators not to commit real credentials.
- Defaults disable several optional/high-impact capabilities.

Recommendations:
- Treat the example file as documentation only; use environment-specific validated configuration schemas.
- Fail closed when required production secrets or security settings are absent.
- Separate development, test, staging, and production configuration.
- Document configuration precedence and secret rotation.
- Add startup diagnostics that report configuration state without printing secret values.
- Remove duplicated or stale example keys and validate the example file in CI.

## 4. Backup and restore

### `scripts/daily_backup.sh` and `scripts/daily_backup.bat`

Observed:
- Create timestamped local archives of the configured Yuki data directory.
- Use a temporary archive name and publish after archive creation/validation.
- Enforce a bounded retention period.
- Reject source/destination nesting.
- Warn operators to keep storage encrypted and maintain another copy separately.

Limitations:
- Unix script validates archive readability via `tar -tzf`; Windows script checks that the ZIP exists and is non-empty. Neither proves application-level restore correctness.
- These scripts do not themselves encrypt archives or transfer them off-host.
- The scripts do not demonstrate scheduling, alerting, restore drills, or verified recovery objectives.
- A file-level archive of a live data directory may capture inconsistent database state unless database files are quiesced or backed up using database-aware mechanisms.

Recommendations:
1. Use database-native consistent backups for SQLite/PostgreSQL and coordinate WAL/sidecar state.
2. Encrypt backups before off-host transfer and keep keys separate from backup storage.
3. Use immutable or object-locked off-site copies with restricted deletion privileges.
4. Monitor backup age, size, completion, and integrity; alert on missed schedules.
5. Run periodic isolated restore drills and verify data/application invariants.
6. Set per-data-class RPO/RTO targets and prove them through drills.
7. Define retention, legal deletion, key rotation, and disaster access procedures.

Desktop backup code has a separate encrypted backup/restore implementation with tests; do not assume that it covers server data or replaces operational database backups.

## 5. systemd and host services

### `deploy/systemd/yuki-host-runtime-inventory.service.example`

Observed:
- Uses a dedicated service user/group, restrictive umask, runtime directory, restart-on-failure, and bounded stop timeout.
- Enables systemd hardening options including `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`, empty capability sets, and restricted address families.

Assessment: useful hardening example for this specific host inventory service.

Gaps:
- It is an example for one service, not a complete deployment definition for the web application, worker fleet, database, or reverse proxy.
- No complete installation/upgrade/rollback automation was verified.
- Environment file ownership, secret provisioning, and service dependency readiness must be documented and validated.

Recommendations:
- Create service units for each production process with separate identities and least-privilege filesystem access.
- Add explicit resource controls, restart backoff, log policy, and readiness integration.
- Keep service credentials and environment files root-owned and inaccessible to unrelated users.
- Validate units with `systemd-analyze security` and test under the actual target OS.

## 6. Observability and incident response

The inspected repository contains readiness and diagnostic components, but no complete production metrics/logging/tracing and alerting stack was established by this review.

Required operational signals:
- Request rate, error rate, latency percentiles, saturation, and availability.
- Authentication failures, rate-limit events, approval denials, and suspicious authorization patterns.
- Database connection pool wait, transaction latency, lock waits, storage usage, and backup freshness.
- Queue depth, oldest task age, retry counts, dead-letter count, worker utilization, and lease expiry.
- Provider errors, timeouts, token/cost consumption, and circuit-breaker state.
- Deployment version, build provenance, configuration version, and restart count.

Security requirements:
- Structured logs with request/trace IDs and tenant-safe correlation.
- Redact secrets, tokens, credentials, personal content, and sensitive tool arguments.
- Define retention and access controls for logs.
- Keep audit events distinct from debug logs and protect them from unauthorized modification.
- Alert on meaningful SLO violations and security events; avoid alerting on every transient error.
- Write incident runbooks for credential compromise, tenant isolation failure, data corruption, provider outage, queue backlog, and deployment rollback.

## 7. Release and deployment safety

The inspected workflow inventory contains desktop package generation and provenance attestation for main-branch artifacts. The packages are explicitly unsigned. No general production web deployment, environment promotion, or automated rollback workflow was found in the inspected inventory.

Recommended release pipeline:
1. Pull request: required tests, lint/type/security checks, migration checks, and review.
2. Main: build once, generate SBOM and provenance, sign artifacts, publish immutable versioned artifacts.
3. Staging: deploy the exact artifact digest; run smoke, migration, auth, and critical workflow tests.
4. Production: explicit approval/environment protection and progressive rollout.
5. Post-deploy: verify SLOs, health, error budget, and critical user journeys.
6. Rollback: restore prior immutable artifact and execute compatible database rollback/forward-fix procedure.

Database migrations must be backward-compatible with the prior application version during rolling deployments. A binary rollback is unsafe if the schema has already been destructively changed.

## 8. Prioritized remediation

| Priority | Work item | Evidence required |
|---|---|---|
| P0 | Define supported production topology and deployable service entrypoints | Reviewed architecture and tested staging deployment |
| P0 | Establish secure secret and environment management | Rotation test and no-secret-leak verification |
| P0 | Establish encrypted off-host backups and restore drills | Successful isolated restore evidence |
| P0 | Add release/deployment promotion and rollback process | Staging-to-production rehearsal |
| P1 | Harden and reproducibly build container images | Pinned digest, image scan, SBOM, signature |
| P1 | Expand CI quality/security gates | Required checks and documented coverage policy |
| P1 | Add production observability and alerting | Dashboards, alerts, and incident exercises |
| P1 | Add PostgreSQL/migration/fault-injection CI | Passing service-backed integration suite |
| P2 | Standardize systemd/container resource policies | Host/container security review |
| P2 | Add SLOs, error budgets, and capacity planning | Measured baseline and operational review |

## 9. Verification checklist

Before calling Yuki production-ready, verify:
- [ ] Production topology, ports, trust boundaries, and data flows are documented.
- [ ] Public ingress uses TLS and trusted proxy/host configuration.
- [ ] Secrets are managed outside source control and rotated.
- [ ] Required CI checks are enforced on protected branches.
- [ ] Dependencies and build artifacts are reproducible and scanned.
- [ ] Container image is hardened, signed, and deployed by immutable digest.
- [ ] Database migrations are tested and compatible with rolling deployment.
- [ ] Backups are encrypted, off-host, monitored, and restore-tested.
- [ ] Metrics, logs, traces, alerts, and incident runbooks are operational.
- [ ] Deployment rollback and disaster recovery have been rehearsed.
- [ ] SLO/RPO/RTO targets are measured rather than merely documented.

## Verification status

This is a targeted repository audit, not an infrastructure assessment. No CI workflow was executed in this review, no image was built or scanned, no service was deployed, and no backup was restored. GitHub branch protection, environment protection, secrets, production topology, external monitoring, and deployed configuration remain unverified.

The repository README currently states that Yuki is not production-ready. This audit does not change that status.
