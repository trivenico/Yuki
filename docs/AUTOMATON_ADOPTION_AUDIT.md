# Automaton architecture adoption review

**Reviewed upstream:** https://github.com/Conway-Research/automaton
**Review date:** 2026-11-10
**Purpose:** Extract architecture patterns for Yuki. This is an independent design review; Yuki does not copy or vendor Automaton.

## Executive summary

Automaton is useful as a reference for a persistent agent runtime composed of a Think/Act/Observe/Persist loop, durable wake events, centralized tool policy, model routing, memory retrieval, agent messaging, and guarded self-modification.

Yuki has partial foundations, but they must not be mistaken for an end-to-end autonomous runtime. The inspected distributed-placement branch contains durable workflow/task and specialist-worker primitives, while the repository README explicitly describes the legacy workflow engine as a bounded allow-listed step runner that does not start schedulers, webhooks, or background jobs. The README also says the prompt guard is heuristic rather than a security boundary, MCP discovery is not wired to runtime execution, plugin imports execute with Yuki's permissions (not a sandbox), and provider usage metering is not yet connected to real inference in that documented revision. Paired-device reservation work is separately in progress in PR #604. The opportunity is to consolidate these partial capabilities behind one durable runtime contract, while preserving those documented limitations until exact integrations and tests prove otherwise.

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

This review compares the repository README (the documented baseline) with the distributed-placement branch and PR #604. They are not identical snapshots. The README explicitly says Yuki is not production-ready and distinguishes implemented foundations from capabilities not yet wired end-to-end; where the README describes a legacy subsystem, this audit does not silently promote it to a newer end-to-end capability.

1. Durable workflow/task primitives exist, but there is not yet one fully unified resumable agent-turn runtime across all interfaces and specialist roles.
2. Scheduler and specialist coordination exist; a single durable heartbeat/wake-event contract needs consolidation and restart semantics.
3. Approval and security modules exist, but policy enforcement must be proven mandatory across every tool/worker execution path.
4. Memory/search exists, but the full hierarchical, provenance-aware lifecycle and automatic consolidation are not yet established.
5. Provider routing and usage metering foundations exist, but routing must be validated across all execution paths with explicit local/cloud policy.
6. Self-improvement should remain a supervised engineering workflow; autonomous production self-modification is not a target.

## Initial source-level authorization inventory

The following execution paths were inspected on `feature/distributed-model-placement`. This is a verified starting inventory, not yet proof that every side-effect path in the repository has been enumerated.

| Entry point / module | Current enforcement | Remaining gateway work |
|---|---|---|
| `yuki.py` `/tool` -> `core/guarded_tool_runtime.py` | Registered tool lookup, `SecurityPolicy.authorize_tool(name)`, bounded arguments/results, metadata audit | Pass authenticated principal, trigger/run/step provenance, typed risk and canonical argument digest into shared policy decision |
| `yuki.py` `/skill invoke` -> `core/approved_skill_runtime.py` -> `core/guarded_skill_runtime.py` | Registry lookup and exact-argument one-shot approval; optional audit | Preserve explicit approval while making policy decision and audit schema common to interactive and worker execution |
| `yuki.py` `/durable run` -> `core/durable_skill_worker.py` | Fixed `skill.invoke` task type, one-attempt semantics, lease heartbeat and guarded skill runtime | Verify worker identity and original owner/trigger provenance at dispatch; prevent task payloads from asserting authority |
| `core/mcp_client.py` | HTTPS host allow-list, public-address check, tool allow-list, explicit callback approval, bounded JSON payloads; results marked untrusted | Route MCP calls through shared policy; bind approvals to server identity, tool schema/version and argument digest; keep returned content untrusted |
| `automation/workflow_engine.py` | Synchronous allow-listed actions, bounded steps/arguments/output, per-step approval callback | If/when connected to runtime scheduling, require shared authorization at each side effect; do not treat workflow approval as a general capability grant |
| `core/plugin_loader.py` / `skills/registry.py` | Discovery disabled by default; explicit trusted module list; validates loaded skill shape | Python imports still execute with process privileges. Keep arbitrary plugins out of process or behind a real OS/container sandbox; policy checks alone do not sandbox code |

### Security boundary conclusions

- The existing guards are useful local controls, but they have different APIs and audit semantics. They are not one centralized policy gateway.
- A successful approval for one skill invocation must not authorize a different argument, tool, server, task, or later run.
- The durable worker must derive authority from persisted authenticated task ownership and trusted dispatch context, not from the skill name or task payload alone.
- MCP discovery metadata and tool results are untrusted. A prompt-injection scan is a signal only; a clean scan must never grant authority.
- The legacy workflow engine is a bounded synchronous runner. Do not describe it as a durable scheduler or claim it currently dispatches arbitrary tools.
- The plugin loader is not a sandbox. A future gateway cannot make in-process untrusted Python safe simply by authorizing its import.

### Gateway integration acceptance criteria

1. Enumerate every call site that can cause a side effect, including direct skill execution, provider actions, browser/network operations, GitHub operations, local file writes, device dispatch, workflow handlers and background workers.
2. Define a typed request contract: authenticated principal, trigger provenance, run/step/task IDs, capability/tool ID, target resource, validated argument digest, declared risk and policy version.
3. Return exactly one of `allow`, `require_approval`, `deny`, or `quarantine`; unknown identities, malformed requests, unavailable policy storage and evaluation errors deny by default.
4. Enforce deny-overrides and scope approvals to the exact action, target, argument digest, principal and expiry. Roles, agent names and model output never grant permissions.
5. Emit a redacted, append-only decision event before execution and a separate outcome event after execution. Audit failure must not turn a denial into an allow.
6. Add adversarial tests for direct-call bypasses, forged principals, replayed approvals, changed arguments, stale worker leases, malicious MCP metadata/results, plugin import side effects, policy-store failure and attempted policy self-modification.
7. Migrate one execution surface at a time; retain existing controls until parity tests prove the shared gateway preserves or strengthens them.

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