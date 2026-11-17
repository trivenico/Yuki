# Yuki production engineering roadmap

This roadmap tracks the gap between today's integrated desktop foundation and the intended personal companion, developer, and defensive cybersecurity product.

## Current delivery priority

**Primary product:** native desktop Yuki for Windows, macOS, and Linux. Desktop is the main daily-use interface and is the release-critical path.

**Secondary product:** Android/mobile-browser access. It is optional and must not block desktop completion. See [Optional Android and mobile access](docs/MOBILE_FIRST_CLOUD_ACCESS.md).

**Training strategy:** first make Yuki useful with a supported existing model/provider. Build a reproducible fine-tuning and evaluation pipeline in parallel once the core conversation runtime is stable. Do not wait for a custom foundation model before delivering the first useful desktop assistant.

### Desktop release sequence

1. Complete and integrate the canonical authenticated conversation runtime and desktop composition.
2. Prove owner sign-in, real-provider conversation, durable history, safe sign-out, and restart recovery.
3. Complete memory consent/inspection, encrypted backup/restore, and recovery acceptance tests.
4. Finish desktop packaging and clean-machine smoke tests on Windows, macOS, and Linux.
5. Wire opt-in push-to-talk voice into the canonical conversation service; microphone access remains off until explicitly activated.
6. Add bounded, permissioned desktop tools and specialist workflows only after their authorization, audit, cancellation, and recovery gates pass.
7. Run a personal-use acceptance review before describing Yuki as ready for daily use.

## Phase 1 — Reliability and safety foundations

- [x] Add a minimal test suite and CI workflow.
- [x] Add a tool-name allow-list boundary.
- [x] Add metadata-only audit records for selected local actions.
- [x] Clarify that provider registry entries are not live integrations.
- [x] Bound and validate learning-history records.
- [ ] Verify the latest expanded test run passes.
- [ ] Add schema/version migration for persistent state.
- [ ] Add a formal threat model and release checklist.
- [ ] Add dependency pinning and static analysis when dependencies are introduced.

## Phase 2 — Real model/provider architecture

- [x] Define provider adapter contracts, timeouts, cancellation, and structured errors.
- [ ] Complete the canonical desktop conversation integration with a real configured provider.
- [ ] Keep credentials out of prompts, logs, repository files, and exception messages.
- [ ] Add explicit provider selection and capability reporting.
- [ ] Preserve offline mock tests and support local models where practical.

## Phase 3 — Agent execution engine

- [x] Add a bounded workflow state machine for perception-to-report lifecycle tracking.
- [x] Connect plan preparation, required-tool policy checks, approval requests, and lifecycle events in a non-executing orchestrator.
- [ ] Connect the workflow to explicit plans, steps, deadlines, and budgets.
- [ ] Connect tool schemas, typed outputs, per-tool permissions, and approval gates to workflow transitions.
- [ ] Add a separately reviewed execution adapter with cancellation, deadlines, bounded retries, and re-authorization immediately before every tool call.
- [ ] Add retry limits, idempotency, cancellation, and recovery from partial failure.
- [ ] Add sandboxed developer tasks and human-reviewable patches.
- [ ] Treat external content as untrusted; defend against prompt injection.

## Phase 4 — Companion and memory

- [ ] Complete durable desktop conversation and memory lifecycle integration.
- [ ] Add memory inspection, export, deletion, retention controls, and consent.
- [ ] Add retrieval tests and relevance evaluation.
- [ ] Make personality adjustable without overriding safety policy.
- [ ] Never infer sensitive personal attributes for memory without clear user intent.

## Phase 5 — Desktop voice and interaction

- [ ] Add opt-in push-to-talk capture; no always-on microphone by default.
- [ ] Wire speech-to-text, canonical conversation response, and optional text-to-speech.
- [ ] Add visible microphone/recording state, cancellation, and stop-playback controls.
- [ ] Test supported audio devices and models on target desktop operating systems.
- [ ] Do not claim wake-word or continuous duplex support until implemented and tested.

## Phase 6 — Defensive cybersecurity and integrations

- [ ] Define authorized target scopes and proof-of-authorization workflow.
- [ ] Begin with passive analysis of user-provided files and dependency/configuration checks.
- [ ] Add evidence-backed findings, severity rationale, and remediation suggestions.
- [ ] Require approval before active scanning, network interaction, or modifications.
- [ ] Add optional, least-privilege account connectors (Google, GitHub, LinkedIn where supported); none is required for Yuki identity or startup.
- [ ] Test against intentionally vulnerable local fixtures; do not target third parties without authorization.

## Phase 7 — Model training and evaluation

- [ ] Build a reproducible managed-GPU fine-tuning pipeline for a suitable open model.
- [ ] Track dataset source, license, consent, preprocessing, and provenance.
- [ ] Version training configuration, tokenizer, base model, checkpoints, and evaluation results.
- [ ] Add held-out capability, safety, regression, latency, and cost evaluations.
- [ ] Require human approval and rollback before promoting a trained checkpoint.
- [ ] Evaluate experimental architecture ideas (including mHC) against a baseline; keep unproven research out of the production runtime.

## Phase 8 — Optional Android access and scale

- [ ] Design an authenticated API over the same application-service boundary.
- [ ] Add Android/mobile-browser chat and read-only status only after desktop release gates pass.
- [ ] Review TLS, session security, CSRF, CORS, rate limits, secret handling, and network exposure before deployment.
- [ ] Evaluate multi-agent collaboration only when it improves measured outcomes.
- [ ] Research emerging and quantum computing approaches without claiming capabilities that have not been demonstrated.

## Release gate

A phase is not complete merely because code has been committed. It needs passing automated tests on the exact commit, review of failure modes, clear capability documentation, and a reproducible verification result. Daily-use readiness additionally requires clean-machine desktop installation, a real-provider end-to-end conversation, durable history, secure recovery, and owner acceptance.
