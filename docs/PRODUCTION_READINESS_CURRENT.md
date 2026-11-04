# Yuki current production-readiness assessment

**Assessment date:** 2026-11-04  
**Audited branch:** `feature/reasoning-context-builder`  
**Audited commit:** `61130a87058a4e8dad4d69aae6d1db4e2e33acf1`  
**Decision:** **Not production-ready. Suitable for continued development and carefully scoped local testing; do not expose as a public multi-user service.**

This document is the current release-readiness snapshot. It supplements, and where dated facts conflict supersedes, the historical `PRODUCTION_BASELINE.md`. It does not certify security or operational fitness.

## Verified progress at this commit

- GitHub Actions workflows associated with this exact commit completed successfully.
- The Python 3.11 quality-gate matrix passed on Ubuntu, Windows, and macOS.
- The run set also reports success for workflow-engine safety, durable workflow store, dependency manifest audit, worker service/provider integration, built-in authentication security, specialist-agent phases, and related capability gates.
- Memory retrieval has bounded candidate scanning, semantic ranking, embedding caching, retrieval diagnostics, bounded rolling p50/p95 samples, deterministic offline evaluation metrics, and real-SQLite integration tests.
- Multi-hop retrieval reuses authenticated actor/agent scope for every hop, validates evidence, bounds query/record budgets, and reports incomplete results when lookahead finds more evidence than the configured result budget.
- Real-SQLite tests cover owner and agent isolation, rejected/superseded record exclusion, and shared-memory permission enforcement.
- A deterministic 100-record cold/warm retrieval baseline test checks scan counts, embedding/cache work, ranking consistency, and privacy-safe diagnostics. Its wall-clock timings are recorded but deliberately not gated on noisy CI hosts.
- The authenticated web chat and provider switching have documented local-MVP boundaries. Public binding is not a production deployment recipe.

CI evidence is tied to this exact commit; later commits must be checked independently.

## Release blockers

### P0 — deployment and trust boundaries

- Do not enable public binding until deployment-specific TLS termination, trusted-host validation, origin/CSRF controls, login throttling, request/body limits, access logging, network policy, and secret provisioning are implemented and tested.
- The web profile is deny-by-default for tools. Do not enable multi-user tool execution until authenticated identity propagation, request-scoped execution, fresh approvals, actor-attributed audit, and per-tool authorization are integrated end-to-end.
- The local Docker runner is not a hardened multi-tenant sandbox. Do not run mutually untrusted users' code on one host under the assumption that current container controls provide a strong tenant boundary.
- Complete an explicit threat model covering prompt injection, malicious skills, provider compromise, SSRF/DNS rebinding, workspace escape, credential leakage, local privilege boundaries, cross-user data access, and supply-chain compromise.
- Commission an independent security review/penetration test before a public production launch.

### P0 — data safety and recovery

- Validate backup contents and privacy boundaries; encrypt backups with authenticated encryption and externally managed keys before storing sensitive user data in them.
- Prove database-consistent backup and restore for every supported persistence backend. Test corrupted archive, wrong key, tampering, partial restore, and disaster-recovery procedures.
- Implement and test schema migrations, compatibility policy, rollback/forward recovery, and startup behavior after interrupted migration.
- Define user data export, deletion, retention, and verified wipe semantics. A retention timestamp or deletion API alone is not proof that backups and caches have been handled correctly.
- Validate PostgreSQL with a real service if it is a supported production backend; do not infer correctness from SQLite tests.

### P1 — reliability and operations

- Run provider-by-provider end-to-end tests with real local Ollama and separately configured cloud credentials, including timeout, cancellation, malformed output, rate limits, authentication failure, and upstream outage.
- Add structured, redacted logs; request/trace correlation; durable actor-attributed security audit; metrics/alerts; health/readiness probes; and operator runbooks.
- Add load and soak tests for API concurrency, streaming, memory retrieval, queue contention, SQLite locking, worker recovery, and resource exhaustion. Define SLOs from observed baselines before enforcing latency targets.
- Validate graceful shutdown, task lease recovery, idempotency, partial failure handling, and safe retry semantics under process crashes.
- Add dependency lock/constraints and reproducible clean-install/package smoke tests for supported Python and operating-system combinations. Review dependency and secret-scan findings rather than treating workflow existence as evidence.
- Test frontend accessibility, browser security, session expiry/revocation, multiple tabs, interrupted streams, and real-device responsiveness.
- Test install, upgrade, rollback, backup restore, and uninstall on clean Windows, macOS, and Linux hosts.

### P1 — product scope and capability truth

- Document which features are genuinely end-to-end, which require optional hardware/models/credentials, and which are scaffolds or fail-closed placeholders.
- Verify the local-first path on a clean machine with Ollama and a supported model, including model acquisition, disk/RAM/VRAM requirements, startup diagnostics, and offline operation after provisioning.
- Validate voice end-to-end on supported hardware, including language behavior, interruption/barge-in, model downloads, privacy controls, and no-background-listener defaults.
- Keep the initial release narrow: a stable companion/chat, reliable memory, a few proven specialist workflows, and explicit approvals. Do not make the number of registered agents a launch criterion.

## Suggested release sequence

1. **Internal alpha:** one trusted operator, loopback-only web/CLI, local data, no untrusted skill imports, no public network exposure. Validate core workflows and recovery.
2. **Private beta:** small, explicitly invited user group only after tenant isolation, authenticated audit, data export/deletion, backup/restore, rate limiting, and real-provider failure tests are complete.
3. **Production candidate:** independent security review, threat-model closure, load/soak tests, restore drill, clean install/upgrade/rollback rehearsal, operational alerts and on-call runbooks.
4. **Production 1.0:** all P0 gates closed, all release-critical P1 gates accepted, exact release commit green across supported platforms, security review findings triaged, and rollback exercised.

## Go/no-go rule

A green CI run is necessary but not sufficient. A release is **NO-GO** if any P0 blocker is open, any critical/high security finding is unresolved, backup restoration has not been demonstrated, or the exact release commit has not passed its required cross-platform gates. Exceptions must be explicit, time-bounded, documented, and approved by the project owner; they must not silently weaken authorization or data-protection controls.

## Delivery estimate policy

Do not promise a calendar date from the current repository snapshot. First close the P0 gates and perform the deployment/security audit; then estimate private-beta work from measured issue sizes and test results. Production timing depends on whether Yuki is intended for a single trusted local operator or a public multi-user service. Those are materially different security and operations scopes.
