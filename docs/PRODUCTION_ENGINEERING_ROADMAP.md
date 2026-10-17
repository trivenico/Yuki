# Yuki production engineering roadmap

Status: active engineering plan. A module is not considered production-ready until it is integrated into the runtime, covered by behavioral tests, and verified in CI.

## Phase 0 — correctness baseline
**Exit gate:** CI passes on the pull-request head; no test is disabled to get green.

- [x] Fix the device scanner registration constructor mismatch.
- [x] Validate provider selection before initializing provider implementations.
- [x] Keep permission tests deterministic by injecting an approval callback in tests only.
- [x] Record CI result from the fix commit: successful.
- [ ] Re-run and verify CI after every subsequent commit in this phase.

## Phase 1 — safety contracts in the running agent
**Exit gate:** tests demonstrate that sensitive output is withheld at the actual skill invocation boundary.

- [x] Apply OutputGuard to user-visible skill messages.
- [x] Inspect structured skill result data as well as message text, so secrets cannot bypass a message-only guard.
- [x] Extend credential-pattern detection to common quoted dictionary/JSON-like key-value output and cover it with runtime contract tests.
- [x] Add behavioral tests for credential/private-key filtering, output truncation, fail-closed governance, recovery approval/verification, strict evaluator status, and agent signature verification where the optional crypto dependency is installed.
- [x] Wire output policy into terminal /ask, /task, allow-listed /ask-cloud, and explicit /voice speak paths.
- [ ] Wire output policy into every other response path, including any future dashboard, API, or automatic spoken-reply paths, before claiming global output protection.
- [ ] Add structured-data redaction beyond simple pattern matching and document false positives/false negatives. Regex output checks are defense-in-depth, not a DLP guarantee.

## Phase 2 — real evaluation and CI quality gates
**Exit gate:** golden scenarios invoke deterministic fixtures or a configured test runtime and fail on behavior regressions.

- [ ] Replace all 20 placeholder scenario inputs/expected phrases with executable fixtures tied to real Yuki behavior.
- [x] Add executable behavior checks for real agent unknown-skill denial, approval denial, secret withholding, and output truncation using the golden case IDs.
- [x] Add output-guard coverage reporting and an 85% minimum CI threshold.
- [ ] Expand coverage thresholds to permissions, agent runtime, providers, and other safety-critical modules.
- [x] Add pip-audit dependency vulnerability scanning to CI.
- [ ] Add secret scanning, pin production dependencies with hashes, and establish a reviewed update policy.
- [ ] Add supported Python version matrix and package/startup smoke tests.
- [ ] Publish test reports as CI artifacts without including prompts, secrets, or personal data.

## Phase 3 — plugin isolation and authorization
**Exit gate:** an untrusted skill cannot access host files, credentials, or network except through explicitly granted capabilities.

- [ ] Define a versioned skill manifest (identifier, version, entry point, permissions, inputs/outputs, dependencies, time/resource budgets).
- [ ] Keep discovery disabled by default and remove wildcard trust from recommended configurations.
- [ ] Execute third-party skills in an isolated worker/container with filesystem allowlists, network policy, resource limits, and a bounded protocol. Do not treat Python imports or a thread timeout as a sandbox.
- [ ] Add tests for path traversal, prompt injection, secret access, SSRF, process exhaustion, malicious imports, and permission bypass.
- [ ] Bind approvals to the exact operation and target, with expiry, replay protection, and audit records.

## Phase 4 — operational reliability and observability
**Exit gate:** external calls have real timeouts, bounded retries, circuit breakers, redacted telemetry, and measurable recovery behavior.

- [ ] Wire OpenTelemetry at provider, skill, memory, and task boundaries; ensure no prompts, credentials, or raw tool arguments are exported.
- [ ] Configure client-level connection/read timeouts. Circuit breakers and retries do not themselves stop a hung blocking call.
- [ ] Add durable recovery budgets/cooldowns and safe recovery handlers with post-recovery verification.
- [ ] Add idempotency keys for retried writes and distinguish retryable from non-retryable errors.
- [ ] Integrate token/cost, latency, error rate, and resource metrics; test telemetry-disabled mode and exporter failure behavior.

## Phase 5 — durable daily-work automation
**Exit gate:** jobs survive restarts and users can inspect, cancel, and revoke them.

- [ ] Add a durable scheduler/job store with idempotency, cancellation, retry policy, and execution history.
- [ ] Build a local authenticated control plane with pairing, session revocation, rate limits, and scoped permissions.
- [ ] Add one high-value messaging/productivity integration at a time; draft/read-only by default for email and calendar.
- [ ] Add memory export/import, backup/restore, retention/deletion controls, and migration tests.
- [ ] Ensure voice outputs are guarded and audio capture is explicit, visible, and consent-based.

## Phase 6 — identity, release integrity, and trust
**Exit gate:** identity is managed, revocable, and verifiable; releases can be reproduced and audited.

- [ ] Build a centralized or local-policy-backed identity registry with key rotation and revocation.
- [ ] Store private keys through OS keychain/secret storage; never commit keys or write them to logs.
- [ ] Sign release artifacts and publish an SBOM and provenance.
- [ ] Document threat model, security reporting, supported platforms, backup/restore, upgrade/rollback, and uninstall.
- [ ] Run an independent security review before describing Yuki as production-ready.

## Phase 7 — public proof of quality
**Exit gate:** public, reproducible benchmark results support any comparative claims.

- [ ] Maintain at least 30 human-reviewed end-to-end tasks spanning coding, defensive security, and everyday workflows.
- [ ] Compare quality, task completion, latency, token/cost, resource usage, privacy routing, and failure recovery against named alternatives.
- [ ] Publish environment, exact versions, scripts, limitations, and failed cases.
- [ ] Do not claim #1 or a production-readiness percentage until independently reproducible results support it.

## Current scope of the active pull request

The current PR is still a draft. OutputGuard now covers YukiCoreAgent.run_skill plus terminal /ask, /task, allow-listed /ask-cloud, and explicit /voice speak outputs. This is not a guarantee for future API/dashboard or automatic voice paths. Four golden case IDs now have executable behavior tests; the remaining golden cases still need behavior fixtures. CI includes pip-audit and an 85% output-guard coverage gate. Do not merge until CI has passed for the latest head SHA.
