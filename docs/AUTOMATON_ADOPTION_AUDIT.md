# Automaton architecture adoption review

**Reviewed upstream:** https://github.com/Conway-Research/automaton
**Review date:** 2026-11-10
**Purpose:** Extract architecture patterns for Yuki. This is an independent design review; Yuki does not copy or vendor Automaton.

## Executive summary

Automaton is useful as a reference for a persistent agent runtime composed of a Think/Act/Observe/Persist loop, durable wake events, centralized tool policy, model routing, memory retrieval, agent messaging, and guarded self-modification.

Yuki already has parts of this foundation: durable tasks/workflows, specialist coordination, local memory, provider routing, approvals, security scanning, and paired-device inference foundations. The opportunity is to consolidate these capabilities behind a durable runtime contract instead of creating parallel subsystems.

## Patterns worth adopting

### 1. Persistent, resumable agent loop

Upstream pattern: a bounded Think -> Act -> Observe -> Persist loop that processes wake events and inbox messages, records state, and returns control rather than assuming one process lives forever.

Yuki direction:
- Represent each agent turn as a durable run with run ID, owner, agent/role, trigger, input references, state, and timestamps.
- Persist checkpoints before and after every external side effect.
- Make tool execution idempotent using run/step IDs.
- Resume interrupted runs from the last committed checkpoint.
- Treat model output as a proposal; validate tool names and arguments against registered schemas.

Acceptance: restart the worker between steps and prove no completed side effect is duplicated and no uncommitted result is accepted.

### 2. Durable heartbeat and wake-event scheduler

Upstream pattern: a heartbeat scheduler that converts timers, inbox arrivals, and system events into wake events.

Yuki direction:
- Use one durable scheduler/outbox rather than in-process timers as the source of truth.
- Support scheduled, event-driven, and manual triggers.
- Persist next_run_at, lease owner, attempt count, last outcome, and deduplication key.
- Add jitter, bounded backoff, missed-run policy, and per-agent concurrency.
- Keep heartbeat work lightweight: inspect state, enqueue actionable work, then return.

Acceptance: restart recovery, duplicate event suppression, bounded catch-up, and deterministic clock-driven tests.

### 3. Centralized tool policy and authority

Upstream pattern: a policy engine evaluates tool identity, risk, input source, authority, and policy rules before execution, with a durable decision record.

Yuki direction:
- Make policy enforcement a mandatory gateway for every tool invocation, including specialist workers and scheduled jobs.
- Resolve authority from authenticated principal and trigger provenance, never from model-provided claims.
- Use deny-overrides semantics; unknown tools, unknown risk, malformed arguments, and policy-store failures fail closed.
- Separate allow, require_approval, deny, and quarantine decisions.
- Audit a redacted argument digest, policy version, matched rules, principal, run/step IDs, and outcome.
- Never let a role/profile grant permissions by itself.

Acceptance: tests prove no direct worker path can bypass the gateway, and external content cannot elevate authority.

### 4. Hierarchical memory with provenance

Upstream pattern: separate memory categories and a retrieval pipeline rather than one undifferentiated prompt transcript.

Yuki direction:
- Working memory: current run context with explicit expiry.
- Episodic memory: timestamped events and outcomes.
- Semantic memory: durable facts with source, confidence, and supersession.
- Procedural memory: reviewed skills and workflows, not arbitrary executable text.
- Relationship memory: user-approved preferences and entity links.
- Store provenance, sensitivity, retention, and deletion lineage for every memory.
- Retrieval must filter by owner and permissions before ranking; memory is context, never authority.

Acceptance: cross-owner isolation, provenance display, conflict/supersession tests, retention enforcement, and prompt-injection tests for retrieved content.

### 5. Model registry and task-aware routing

Upstream pattern: a registry plus routing policy that selects providers/models by task type and capability.

Yuki direction:
- Normalize provider capabilities, context limits, modalities, structured output, streaming, cancellation, and usage metadata.
- Route local-first when suitable; fall back to cloud only under explicit user policy.
- Select by task requirements, model quality, latency, availability, and actual provider price.
- Track usage for transparency, not as an arbitrary owner quota.
- Keep provider billing, provider rate limits, and physical capacity separate from Yuki access policy.

Acceptance: deterministic routing tests, provider failover tests, exact-vs-estimated usage labeling, and no hidden cloud escalation.

### 6. Guarded self-improvement

Upstream pattern: modification audit, path validation, protected files, snapshots, and rollback-oriented workflows.

Yuki direction:
- Have agents create proposals/patches in isolated branches or workspaces.
- Protect authentication, authorization, policy enforcement, audit, secrets, data migrations, and deployment credentials.
- Validate path containment including symlinks and race-resistant file operations.
- Require tests, static analysis, dependency/security checks, human approval, and rollback plan before merge/deploy.
- Record immutable audit events and artifact hashes.
- Never permit an agent to change the policy that authorizes its own changes.

Acceptance: tests prove protected-path edits, traversal, symlink escapes, missing tests, and policy tampering are rejected.

## Patterns not to copy

- Autonomous wallet funding, token economics, or spending loops: irrelevant to Yuki's personal-agent goal and introduce financial risk.
- Self-replication or spawning agents without explicit owner policy and resource controls.
- Unrestricted shell execution or arbitrary installed code.
- Agent-editable security invariants, credentials, policy engine, or audit trail.
- A single-process in-memory heartbeat as the durability authority.
- Fixed tool-call ceilings as a substitute for loop detection and resource safety. Yuki should use cycle detection, cancellation, deadlines, and bounded per-run resource budgets without imposing arbitrary lifetime usage quotas.

## Gaps observed in Yuki

This review is based on the repository README and distributed inference/job/scheduler files inspected during this work. The README explicitly says Yuki is not production-ready and distinguishes implemented foundations from capabilities not yet wired end-to-end.

1. Durable workflow/task primitives exist, but there is not yet one fully unified resumable agent-turn runtime across all interfaces and specialist roles.
2. Scheduler and specialist coordination exist; a single durable heartbeat/wake-event contract needs consolidation and restart semantics.
3. Approval and security modules exist, but policy enforcement must be proven mandatory across every tool/worker execution path.
4. Memory/search exists, but the full hierarchical, provenance-aware lifecycle and automatic consolidation are not yet established.
5. Provider routing and usage metering foundations exist, but routing must be validated across all execution paths with explicit local/cloud policy.
6. Self-improvement should remain a supervised engineering workflow; autonomous production self-modification is not a target.

## Recommended implementation sequence

1. Policy gateway audit and enforcement — establish one mandatory tool authorization path and close bypasses.
2. Durable agent run/checkpoint contract — unify task, workflow, specialist, and inference job identities.
3. Wake-event scheduler — durable timers, inbox triggers, dedupe, leases, restart recovery.
4. Memory provenance and lifecycle — typed stores, retrieval permissions, consolidation and retention.
5. Provider/model capability registry — local-first task routing and unified usage reporting.
6. Supervised improvement pipeline — proposal, isolated patch, automated validation, review, rollback.

## Relationship to PR #604

PR #604 builds authenticated device resource reservations and scheduler-side placement. It does not yet claim end-to-end worker dual-lease execution. Keep resource ownership, fencing, and recovery as a separate prerequisite to dispatching work across devices. The Automaton-inspired runtime should consume that safe execution contract rather than bypass it.

## Sources inspected

- Automaton repository: https://github.com/Conway-Research/automaton
- src/agent/loop.ts — bounded agent loop and runtime composition
- src/agent/policy-engine.ts — centralized policy decisions and authority derivation
- src/agent/injection-defense.ts — untrusted input and tool-result handling
- src/inference/router.ts — model registry and task-aware routing
- src/self-mod/code.ts — protected paths, modification audit, and validation
- Yuki README.md, core/inference_jobs.py, automation/local_inference_worker.py, and reservation/scheduler implementation

This document records design findings and proposed acceptance criteria. It does not claim these features have been implemented by writing this audit.