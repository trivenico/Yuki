# Production Engineering Phase 140 — Integration Audit and Release Gates

## Status

**Audit opened; implementation and release qualification are not complete.** This document records repository evidence and required acceptance gates. It is not a claim that tests passed, security controls are fully integrated, or production is approved.

## Evidence observed

- The default-branch README explicitly says Yuki is not production-ready.
- The README describes the model-guided task loop as bounded and approval-gated, but not a production-grade native tool-calling runtime.
- The README says trusted Python plugins execute with Yuki's process permissions and are not sandboxed. Plugin discovery is disabled by default.
- MCP discovery/call primitives are not wired into the normal runtime command/registry/UI path.
- Token usage records are not connected to real provider usage in the documented revision.
- Voice, video, smart-home, vehicle, financial, messaging, and other registered capabilities include scaffolds or optional integrations; registration must not be treated as end-to-end delivery.
- PR #264 (ASGI authentication and CSRF) explicitly leaves host server/login wiring and terminal CLI login as follow-up work; merge only after exact-head CI passes.
- PR #29's description requires draft status until independent security review and cross-platform CI; it documents that the existing workflow is a single Linux job and that a final audit failure cannot undo an already executed side effect.
- PR #1's description says approval/execution integration is not universal, OS-level isolation remains open, and its current head had no workflow run/status returned when that description was written.
- Several historical PR descriptions explicitly say their validation was pending or tied to an older commit. Old green runs must not be reused as evidence for newer heads.

## Phase 140 acceptance checklist

### P0 — Authoritative integration inventory
- [ ] For every open PR, capture base/head refs and SHAs, changed paths, draft state, mergeability/conflicts, reviews, unresolved threads, required checks, and latest check results.
- [ ] Verify every green check is for the exact current PR head SHA.
- [ ] Map overlapping files and stacked dependencies; do not merge a dependent PR before its prerequisites.
- [ ] Keep security-sensitive PRs draft until independent review and required checks are complete.

### P0 — One enforceable authorization boundary
- [ ] Inventory every entry point that can invoke a skill, worker, browser, MCP tool, filesystem mutation, subprocess, or external side effect.
- [ ] Route all sensitive execution paths through the same validated manifest/capability/approval/dispatch boundary; reject legacy bypass paths.
- [ ] Bind each approval to canonical action content, actor identity, session, expiry, and one-time consumption.
- [ ] Require audit persistence before execution and fail closed if the pre-execution audit fails.
- [ ] If post-execution audit persistence fails, report the side effect as uncertain/audit-failed and require reconciliation; never claim rollback.
- [ ] Test parallel approval/replay attempts across processes, not only threads within one process.

### P0 — Authentication and CSRF
- [ ] Wire ASGI authentication middleware into the actual application entry point and login/session lifecycle.
- [ ] Ensure authenticated actor identity, not a client-supplied actor string, is passed into approval and execution.
- [ ] Enforce CSRF checks on cookie-authenticated state-changing routes.
- [ ] Define CLI identity/session behavior separately; do not imply browser-session authentication covers terminal execution.
- [ ] Add integration tests for anonymous access, wrong actor/session, expired sessions, CSRF failure, and approval replay.

### P0 — Process and plugin isolation
- [ ] Keep arbitrary/unreviewed Python plugin imports disabled.
- [ ] Verify subprocess execution uses fixed argv, no shell, bounded time/output, environment allow-listing, and descendant cleanup.
- [ ] For untrusted code, require a real OS/container sandbox with least privilege, read-only filesystem by default, disabled network unless explicitly allowed, resource quotas, and pinned image digests.
- [ ] Add opt-in real-runtime integration tests and document platform limitations; mocked command-construction tests are not proof of isolation.
- [ ] Keep production execution blocked where the required isolation cannot be verified.

### P1 — Durable task lifecycle
- [ ] Trace task submission through queue, worker, approval, execution ledger, checkpoint, cancellation, and reconciliation.
- [ ] Test process termination before side effect, after side effect but before result persistence, and during final audit.
- [ ] Never automatically replay an ambiguous external side effect; require target-specific evidence and operator reconciliation.
- [ ] Test duplicate idempotency keys, stale revisions, concurrent workers, cancellation races, and graceful shutdown.
- [ ] Document that exactly-once effects are not guaranteed unless the downstream target supports idempotency.

### P1 — Observability and dependency health
- [ ] Instrument real provider requests, task/skill execution, queue latency, failures, and approval outcomes.
- [ ] Record bounded, redacted metadata only; exclude prompts, responses, secrets, and raw action arguments.
- [ ] Distinguish liveness from readiness and verify dependencies with timeouts.
- [ ] Add operational dashboards/alerts and documented backup, restore, and incident procedures before production deployment.

### P1 — Release qualification
- [ ] Run full unit tests, compile checks, static analysis, dependency/security audit, and supported-platform CI on the exact release candidate.
- [ ] Exercise clean install, configuration validation, graceful shutdown, crash recovery, backup/restore, and rollback.
- [ ] Review branch protection and required status checks; the application-level PR gate is not a replacement for GitHub enforcement.
- [ ] Complete an independent security review and record accepted residual risks.
- [ ] Update capability claims so every feature is labelled implemented, partial, prototype, or unavailable.

## Merge policy

Do not bulk-merge open PRs. Do not merge a security-sensitive PR based only on its description or a green run from an older SHA. Prefer small dependency-ordered batches, verify the resulting default-branch commit, and rerun the full required checks after integration.

## Exit criteria

Phase 140 is complete only when the checklist has linked evidence for each item, all P0 items are closed, the release candidate's exact-head CI is green, and an authorized human approves the release. Until then, Yuki remains **not production-ready**.
