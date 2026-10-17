# Yuki production engineering roadmap

This roadmap tracks the gap between today's terminal prototype and the intended companion, developer, and defensive cybersecurity product.

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

- [ ] Define provider adapter contracts, timeouts, cancellation, and structured errors.
- [ ] Implement one provider using its official API and supported authentication only.
- [ ] Keep credentials out of prompts, logs, repository files, and exception messages.
- [ ] Add explicit provider selection and capability reporting.
- [ ] Preserve offline mock tests and support local models where practical.

## Phase 3 — Agent execution engine

- [x] Add a bounded workflow state machine for perception-to-report lifecycle tracking.
- [ ] Connect the workflow to explicit plans, steps, deadlines, and budgets.
- [ ] Connect tool schemas, typed outputs, per-tool permissions, and approval gates to the workflow transitions.
- [ ] Add retry limits, idempotency, cancellation, and recovery from partial failure.
- [ ] Add sandboxed developer tasks and human-reviewable patches.
- [ ] Treat external content as untrusted; defend against prompt injection.

## Phase 4 — Companion and memory

- [ ] Separate short-term context, long-term memories, and task history.
- [ ] Add memory inspection, export, deletion, retention controls, and consent.
- [ ] Add retrieval tests and relevance evaluation.
- [ ] Make personality adjustable without overriding safety policy.
- [ ] Never infer sensitive personal attributes for memory without clear user intent.

## Phase 5 — Defensive cybersecurity

- [ ] Define authorized target scopes and prove-of-authorization workflow.
- [ ] Begin with passive analysis of user-provided files and dependency/configuration checks.
- [ ] Add evidence-backed findings, severity rationale, and remediation suggestions.
- [ ] Require approval before active scanning, network interaction, or modifications.
- [ ] Test against intentionally vulnerable local fixtures; do not target third parties without authorization.

## Phase 6 — User experience and integrations

- [ ] Build a web dashboard with task state, permission prompts, audit history, and settings.
- [ ] Add desktop/terminal consistency and accessible interaction patterns.
- [ ] Add supported browser automation with visible controls and an isolated session.
- [ ] Add secure export, backup, and restore.

## Phase 7 — Evaluation and scale

- [ ] Create benchmark tasks for companion usefulness, developer correctness, and defensive security quality.
- [ ] Measure completion rate, cost, latency, safety violations, and recovery.
- [ ] Add regression suites and release gates.
- [ ] Evaluate multi-agent collaboration only when it improves measured outcomes.
- [ ] Research emerging and quantum computing approaches without claiming capabilities that have not been demonstrated.

## Release gate

A phase is not complete merely because code has been committed. It needs passing automated tests, a review of failure modes, clear capability documentation, and a reproducible verification result.
