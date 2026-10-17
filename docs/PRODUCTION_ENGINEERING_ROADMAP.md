# Yuki production engineering roadmap

Status: active engineering plan. A module is not considered production-ready until it is integrated into the runtime, covered by behavioral tests, and verified in CI.

## Phase 0.5 — onboarding and configuration
**Exit gate:** a first-run user can select a provider without editing YAML or exposing credentials in terminal output.

- [x] Add `python main.py --setup` for mock/demo, local Ollama, and hybrid provider configuration.
- [x] Keep API credentials out of YAML, hide interactive key entry, use an ignored local `.env`, and set owner-only file permissions on POSIX.
- [x] Load supported local provider settings without overriding shell environment variables.
- [x] Add behavioral tests for provider selection, credential separation, and environment precedence.
- [ ] Add install/bootstrap automation, model installation guidance, dependency locking, and tested Windows/macOS/Linux onboarding.
- [ ] Add credential rotation/removal UX and validate provider connectivity from the wizard without leaking sensitive diagnostics.

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
- [x] Wire output policy into the provider-independent `YukiAgent.ask` boundary as well as terminal /ask, /task, allow-listed /ask-cloud, and explicit /voice speak paths.
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

- [x] Add a versioned typed skill manifest contract with declared capabilities, bounded input/output schemas, risk, time/output budgets, and parallel-safety checks (not yet integrated into the plugin dispatcher).
- [x] Add `core/skill_dispatch.py` to enforce registered manifests, explicit capability grants, input/output validation, and approval for medium/high-risk or side-effecting capabilities (standalone dispatcher; existing runtime/plugin loader is not yet wired to it).
- [x] Add `SkillDispatcher.register_subprocess()` to route manifest-validated handler calls through the bounded JSON subprocess executor with fixed deployment-configured argv/env.
- [x] Add `YukiCoreAgent.dispatch_manifest_skill()` as a guarded live-agent entrypoint that invokes the dispatcher, requires pre-issued action-bound approval for consequential actions, and fails closed when no dispatcher is configured.
- [x] Add `core/manifest_task_agent.py`, a bounded model-guided plan/act/observe loop that lists only granted manifest skills and routes calls through `YukiCoreAgent.dispatch_manifest_skill()`; consequential actions pause with `approval_required` instead of executing.
- [x] Add explicit `resume_approved()` for a trusted approval UI to resume a pending action with a one-time token bound to the exact skill, arguments, actor, and session.
- [ ] Wire the manifest planner into the main CLI/API/task entrypoints; the existing `ApprovedTaskAgent`, legacy `run_skill()` registry path, and plugin loader remain separate and must not be considered sandboxed.
- [ ] Replace or migrate legacy task-planner call sites to the manifest planner, integrate plugin discovery only after trust/signature checks, and add OS-level sandbox restrictions before third-party execution.
- [ ] Add dependency declarations, signature/trust verification, and isolated execution.
- [x] Keep discovery disabled by default and reject wildcard trust grants in the loader.
- [ ] Remove wildcard entries from example/user configuration and document exact module allowlisting.
- [ ] Execute third-party skills in an isolated worker/container with filesystem allowlists, network policy, resource limits, and a bounded protocol. Do not treat Python imports or a thread timeout as a sandbox.
- [x] Add regression tests for disabled discovery, missing allowlist, and wildcard trust rejection.
- [ ] Add tests for path traversal, prompt injection, secret access, SSRF, process exhaustion, malicious imports, and permission bypass.
- [x] Add `core/action_approvals.py` one-time tokens bound to canonical action hash, actor, session, expiry, and single consumption.
- [x] Add `SQLiteActionApprovalStore` for restart-persistent one-time tokens, transactional single consumption, expiry cleanup, and storage of token hashes rather than raw bearer tokens; actor/session authentication and audit events remain external requirements.
- [x] Add tests for persistence across store recreation, single-use consumption, token-hash persistence, action/identity mismatch, and denied issuance.
- [x] Enforce one-time action-bound approval tokens inside the standalone `SkillDispatcher`; token consumption binds exact canonical action, actor, session, and expiry.
- [x] Add `core/approval_flow.py` to store a bounded server-side snapshot of the exact pending action, present a detached action payload to a trusted confirmation callback, consume pending IDs once, and issue/consume a one-time action-bound token only after explicit approval.
- [x] Add `core/approval_audit.py` with bounded SQLite audit events for requested/approved/denied/executed/failed outcomes; hash actor/session/pending identifiers, hash canonical action data, and reject arbitrary free-text metadata.
- [x] Connect the approval coordinator to the optional persistent pending store and audit log; fail closed before execution when request/approval audit persistence fails, and test the complete coordinator flow with SQLite-backed pending actions and audit events.
- [ ] Wire the coordinator into main CLI/API and the live `PermissionGate`; configure SQLite pending/token/audit stores from protected workspace paths and derive actor/session from authenticated identity. Pending-action and token stores are restart-persistent primitives, but the application does not yet configure them; actor/session values must come from trusted identity.

## Phase 4 — operational reliability and observability
**Exit gate:** external calls have real timeouts, bounded retries, circuit breakers, redacted telemetry, and measurable recovery behavior.

- [x] Wire privacy-conscious telemetry around the live `YukiCoreAgent.run_skill` boundary with result status/timing metadata, credential-shaped value redaction, and a non-fatal local event log.
- [x] Add redacted timing/status spans for direct provider asks, bounded task-planner calls, explicit cloud requests, and core skill invocation.
- [ ] Configure a production exporter and extend telemetry to memory, voice, all external tools, and full task lifecycle; verify no prompts, credentials, or raw tool arguments are exported.
- [ ] Configure client-level connection/read timeouts. Circuit breakers and retries do not themselves stop a hung blocking call.
- [x] Add CLI `/health` checks for workspace access, provider configuration, local Ollama model inventory, plugin trust policy, and configured Piper model presence; checks are read-only and network access is restricted to loopback Ollama.
- [ ] Add durable recovery budgets/cooldowns and safe recovery handlers with post-recovery verification.
- [x] Add operation-level idempotency keys and bounded retry-state transitions to the durable job-store primitive; callers must still provide idempotency at external side-effect boundaries.
- [x] Add `core/task_worker.py`, a single-process worker that claims durable jobs, invokes explicitly registered trusted handlers, persists results, bounds retries, and redacts handler exceptions; this is not a sandbox and has no hard timeout/shutdown loop yet.
- [ ] Integrate worker with task API/CLI, graceful shutdown, durable recovery scheduling, and operation-specific idempotency policies. Do not register untrusted plugins.
- [ ] Integrate token/cost, latency, error rate, and resource metrics; test telemetry-disabled mode and exporter failure behavior.

## Phase 5 — durable daily-work automation
**Exit gate:** jobs survive restarts and users can inspect, cancel, and revoke them.

- [x] Add a transactional SQLite durable job store with bounded JSON payloads/results, idempotency keys, attempt budgets, cancellation requests, and stale-worker recovery primitives (not yet wired to a background scheduler or task runtime).
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
