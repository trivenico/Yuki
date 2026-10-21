# Phase 22 — upstream agent adoption plan

Status: proposed integration strategy; no upstream runtime has been vendored by this phase.

## Decision

Do not replace Yuki with a fork of Hermes, OpenClaw, or OpenJarvis and do not wholesale-copy their repositories. Preserve Yuki as the product/runtime and integrate small, reviewed capabilities behind Yuki interfaces. A fork is appropriate only if Yuki intends to maintain a long-lived upstream variant with its own release and patch-merging process.

## Candidate sources

- Hermes Agent: https://github.com/NousResearch/hermes-agent — repository metadata reports MIT.
- OpenClaw: https://github.com/openclaw/openclaw — repository metadata reports MIT.
- OpenJarvis: https://github.com/open-jarvis/OpenJarvis — repository metadata reports Apache-2.0.

Repository-level license metadata is not sufficient to clear every file, skill, model, asset, or dependency. Before copying source, pin the exact commit; inspect file headers and dependency licenses; preserve copyright/license notices; satisfy Apache-2.0 notice and modification-marking requirements; record provenance and local modifications.

## Integration order

### P0 — stabilize the Yuki baseline
- Require a green supported-Python CI matrix before adding new runtime dependencies.
- Fix missing-module, constructor, provider-routing, and interactive-test failures.
- Review open security PRs independently; do not treat a module import as proof of a secure runtime path.

### P1 — adopt contracts, not whole frameworks
- Define a versioned skill manifest: stable ID, version, entry point, input/output schemas, capabilities, risk, timeout, output budget, provenance, and license.
- Keep third-party skills disabled until reviewed and explicitly enabled.
- Preserve default-deny capabilities, per-action human approval for privileged effects, bounded output/time, and an audit trail.
- Add a provider contract with explicit local/cloud routing; no implicit cloud fallback.

### P2 — incremental capabilities
1. Hardware/resource doctor and local model readiness report.
2. Durable, restart-safe scheduler with idempotency and visible job history.
3. Portable skill import/export with signature/provenance checks and dependency isolation.
4. One messaging adapter with opt-in pairing, least-privilege scopes, rate limits, and revocation.
5. Sandboxed browser/computer use only after threat-model and integration tests.

## Required acceptance criteria for every imported capability

- Upstream commit and license provenance recorded.
- Dependency and transitive license review completed.
- No secrets, tokens, personal memory, or provider outputs in logs.
- Adversarial tests cover malformed input, prompt injection, permission bypass, timeouts, output flooding, and cleanup.
- Unit tests plus integration tests pass on supported operating systems.
- Feature readiness is documented as tested, partial, or unsupported; a README or module alone is not evidence of production readiness.

## Current gate

PR #52 is the current CI stabilization workstream. Its latest workflow runs were queued at the time of this update, and a prior run failed. Do not begin upstream runtime imports or claim production readiness until the baseline is green and the approval/execution path has been reviewed.
