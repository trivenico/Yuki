# Yuki Engineering and AI/ML Gap Audit

**Audit date:** 2026-10-29  
**Repository:** `trivenico/Yuki`  
**Audited integration snapshot:** `5614309e6697e309e89637c43eefd68db81156fe` (PR #364 head at audit start)  
**Scope:** source-level architecture, runtime, provider/model selection, safety boundaries, memory, skills/MCP, perception/voice, packaging and CI evidence. This is a first-pass engineering audit of the repository's main subsystems, not a line-by-line certification or penetration test. No local machine, real Ollama inference, desktop device, or camera/screen session was available for this review.

## Executive summary

Yuki has good foundations: local-first provider defaults, fail-closed approval primitives, bounded model-guided skill invocation, a durable task ledger, guarded extension import, a constrained MCP HTTP client, and cross-platform packaging workflows. The README accurately says the project is **not production-ready**.

The main risk is integration maturity. There are many capability modules and specialist-agent workflows, but the product needs a single measurable execution contract that connects planning, typed tools, approvals, durable state, recovery, verification, memory, UI and provider adapters. Feature count and the number of specialist roles are not a substitute for reliable end-to-end task completion.

**Recommendation:** stabilize one narrow end-to-end vertical slice first (task request → typed plan → policy decision → approved tool call → durable event/checkpoint → observed result → verification → user-visible trace), then expand capability breadth behind the same contracts.

## Evidence and scope caveats

Primary source snapshot reviewed:
- `README.md`: explicitly calls out the bounded JSON-based task loop and multiple missing production capabilities.
- `main.py`: central runtime composition and command loop; imports provider, memory, planning, permissions, skills, voice, perception, security-lab and task modules.
- `core/agent.py`, `core/task_agent.py`, `core/permissions.py`, `core/guarded_skill_runtime.py`: bounded registered-skill execution and approval gates.
- `core/task_ledger.py`, `core/vector_memory.py`, `companion/memory.py`: durable task metadata and local lexical memory.
- `providers/factory.py`, `providers/ollama.py`, `providers/openai_compatible.py`, `providers/hybrid.py`: provider construction, transport, and routing.
- `core/extension_store.py`, `core/mcp_client.py`: extension and remote tool security boundaries.
- `core/perception.py`, `ui/desktop_app.py`: local perception primitives and desktop shell.
- `core/readiness.py`, `scripts/smoke_test_ollama.py`: diagnostics and real-inference acceptance script.
- PR #364, #370, #362 and #363: integration status and provider/model selection changes.

The absence of a feature in this report means it was not established as end-to-end working by this source review; it does not prove no partial implementation exists elsewhere. CI results apply only to the exact commit tested. The current snapshot's latest empty-override fix still needs fresh exact-head CI; prior green runs must not be reused as evidence.

## Risk-ranked findings

### P0 — Release blockers

#### P0.1: No end-to-end acceptance evidence on supported machines
**Evidence:** README marks Yuki not production-ready. PR #364 requires real Ollama inference on Windows and Linux, desktop permission checks, shared CLI/desktop paths, shutdown, and installer upgrade/uninstall acceptance. The smoke-test script is designed for real inference, but this audit did not execute it.

**Required work**
1. Run the smoke test against the configured local Ollama endpoint/model on Windows and Linux.
2. Execute a signed-off desktop acceptance matrix: first run, model missing, model unavailable, offline mode, provider failure, cancel, restart, upgrade, uninstall/reinstall, existing config preservation, screen/camera permission grant/revoke.
3. Capture test logs and artifact hashes without recording secrets or raw private prompts.
4. Keep release status blocked until all required scenarios pass.

#### P0.2: Tool execution needs a clear isolation contract
**Evidence:** `core/guarded_skill_runtime.py` states the approval boundary does not sandbox skill code. GitHub skill import intentionally installs only `SKILL.md`, which is a good safe default, but any future executable tool/skill needs a separate trust and isolation model.

**Required work**
- Separate instruction-only skills from executable tools in types, manifests, UI and policy.
- For executable actions, use a per-task restricted workspace and subprocess/container boundary with time, memory, filesystem and network budgets.
- Bind approval to a canonical action ID, exact arguments, resource scope, and policy version; invalidate approvals when any field changes.
- Require an explicit confirmation for destructive or external side effects, and verify the resulting state after execution.
- Add adversarial tests for path traversal, symlinks, argument mutation, approval replay, timeout, cancellation and partial failure.

#### P0.3: Integration branch is broad and overlapping PRs carry unique behavior
**Evidence:** PR #364 is a broad integration (about 99 files at the audit snapshot). PR #370 contains provider catalog/model-selection behavior; PRs #362/#363 are stacked and the #363 desktop bridge constructs a provider/agent directly, unlike #364's single child-process runtime. PR #364's own description warns against merging the parallel desktop execution path wholesale.

**Required work**
- Preserve the single authoritative guarded runtime; do not merge a second direct-to-provider desktop path.
- Build a behavior/test inventory across #358, #362, #363, #364 and #370; port only unique functionality into the chosen runtime.
- Require an independent reviewer to review the aggregate diff, permission boundaries, provider factory, packaging, and config migration.
- Merge only after exact-head CI and review, not because GitHub reports `mergeable: true`.

### P1 — Core architecture and reliability

#### P1.1: Promote the bounded JSON loop into a typed, observable agent runtime
**Evidence:** README explicitly says the task loop is bounded JSON-based and not a production-grade native tool-calling/ReAct runtime. `core/task_agent.py` limits steps and uses registered skills and approvals. This is a sensible safe baseline but limits complex multi-step work.

**Required work**
- Define typed contracts for `Task`, `PlanStep`, `ToolDefinition`, `ToolCall`, `Approval`, `ToolResult`, `VerificationResult` and `TaskEvent`.
- Add provider capability negotiation: native function/tool calling where supported, strict JSON fallback where not; never let provider differences bypass policy.
- Make planning, execution, observation, verification and finalization explicit states.
- Add bounded replanning on recoverable errors, cancellation, per-step deadlines, global budgets, retry classification and idempotency keys.
- Require postcondition verification before marking a task completed; distinguish “tool returned success” from “goal achieved.”
- Keep model-generated plans untrusted and validate every tool call against schemas and policy before execution.

#### P1.2: Durable ledger is not by itself durable execution
**Evidence:** `core/task_ledger.py` stores validated task metadata/status transitions and explicitly does not execute tools or grant permissions. LangGraph's documented production pattern combines checkpoints, state persistence, interrupt/resume and fault recovery.

**Required work**
- Persist each step input/output reference, approval, tool invocation ID, retry count, timeout, event sequence and verification result.
- Support restart recovery, pause/resume, cancellation and recovery from a crash between side effect and result persistence.
- Define at-least-once delivery semantics and idempotency for side-effecting tools.
- Add crash-injection tests at every state transition, especially awaiting approval and awaiting verification.
- Add SQLite migrations/schema versioning and corruption/backup recovery tests.

#### P1.3: Multi-agent coordination must have measurable value and strict budgets
**Evidence:** `core/agent_council.py` describes parallel specialists as advisory; they do not execute tools or grant permissions. This is a good separation. It currently defines a small fixed specialist set rather than a validated 100-agent production fleet.

**Required work**
- Make agent roles declarative and task-scoped, with explicit input/output schemas and tool capabilities.
- Use a supervisor to decide whether delegation is worth the latency/cost; do not spawn agents for simple tasks.
- Isolate context, memory, credentials and workspaces per agent/task.
- Define concurrency, deadline, token/cost, retry and fan-out limits.
- Add conflict resolution, evidence aggregation, disagreement handling and a single final verifier.
- Benchmark single-agent vs multi-agent success rate, cost, latency and regression rate before enabling more agents.

#### P1.4: Provider catalog, settings and runtime factory need one source of truth
**Evidence:** `providers/factory.py` implements a number of adapters. `core/settings_store.py` contains a provider tuple and model-setting mapping, while PR #370 introduces a shared catalog and safe selection helpers on a stacked branch. PR #362 documents catalog-only providers that are not implemented by runtime adapters.

**Required work**
- Integrate the shared catalog into the authoritative runtime branch, then remove duplicated provider lists.
- Model each adapter's actual capabilities: tool calling, streaming, structured output, vision, embeddings, cancellation, context window, and local/cloud privacy class.
- Keep “visible in catalog” separate from “implemented”, “configured”, “reachable”, and “verified”.
- Use one model ID validator and one configuration precedence contract across CLI, desktop, environment, and provider factory.
- Add contract tests per adapter, including malformed responses, timeouts, rate limits, invalid credentials, redirects, no-network tests and no-silent-fallback guarantees.
- Use explicit routing policies for hybrid mode, including data classification and user consent; default sensitive work to local.

#### P1.5: Production observability and evaluation are not yet demonstrated
**Evidence:** The reviewed core has task statuses and diagnostic output, but the reviewed materials do not establish a unified trace/evaluation system across model calls, tool calls, approvals, memory retrieval, cost and final outcomes.

**Required work**
- Emit structured local-first events with task ID, step ID, provider/model, latency, token/cost estimate where available, tool result category, approval decision and verification result.
- Redact API keys, tokens, private file contents, screen/camera payloads and sensitive prompt fields by default.
- Add trace replay from recorded non-sensitive fixtures, export/delete controls, log retention settings, and opt-in remote telemetry only.
- Create a golden task set for CLI, coding, file operations, memory retrieval, skill install, provider failure, prompt injection and desktop workflows.
- Track task success, verified completion, tool-call correctness, unsafe action block rate, hallucinated completion, latency, cost and recovery rate across model versions.

### P1 — Security, privacy and autonomy

#### P1.6: Security posture should be represented as an auditable capability policy
**Evidence:** `core/permissions.py` fails closed without an approver; `core/mcp_client.py` uses HTTPS allowlisting, public-IP checks, pinned resolution and redirect blocking; `core/extension_store.py` validates manifests and pins GitHub skill source to a commit SHA. These are good defensive controls.

**Required work**
- Add a machine-readable capability matrix for filesystem, process, network, browser, camera, screen, microphone, messaging and external APIs.
- Enforce least privilege per tool, task and specialist; never assume a skill's natural-language description is a security policy.
- Audit MCP tool schemas and capabilities, not just server URLs. Pin reviewed schemas and require re-approval when a schema or permissions change.
- Add security tests for prompt injection from documents/webpages/skill content, secret exfiltration, SSRF/DNS rebinding, redirects, malicious server responses, and approval confusion.
- Make audit output explain why each capability is enabled, which identity can use it, and what resource scope is authorized.
- Add dependency/license/SBOM checks and a documented vulnerability-response process.

#### P1.7: Privacy promise needs full lifecycle controls
**Evidence:** `core/perception.py` documents local, explicit one-shot capture and says it does not perform background recording, networking, OCR or image upload. The current perception API does not itself make Yuki a visual assistant.

**Required work**
- Add visible, revocable permission indicators for screen, camera, microphone, browser and desktop control.
- Add per-capability data-flow disclosures: local processing, cloud provider routing, retention, and whether a payload leaves the device.
- Provide memory inspect/edit/delete/export and retention controls; ensure deletion covers derived summaries and indexes.
- Add a private mode that disables durable memory, proactive background tasks and cloud routing for the current session.
- Test permission revocation while an action is queued or running.

### P1 — Memory and knowledge

#### P1.8: Current “VectorMemory” is lexical memory, not semantic vector search
**Evidence:** `core/vector_memory.py` states “Local long-term lexical memory; no embeddings or network calls.” It tokenizes text and ranks overlap. That is predictable and privacy-preserving, but not semantic retrieval.

**Required work**
- Rename or document the component to avoid implying embeddings it does not use.
- Separate short-term conversation state, durable user facts/preferences, episodic task memory, and indexed knowledge.
- Add source, timestamp, confidence, sensitivity, consent, provenance and expiry to memory records.
- Introduce optional local embeddings and a local vector index behind an interface; keep lexical fallback and never require cloud embeddings for local-first mode.
- Evaluate retrieval precision/recall, stale memory, conflicting facts, prompt injection in retrieved content, and memory poisoning.
- Require user approval before promoting sensitive or inferred claims to durable memory; support correction and deletion.

### P2 — Product capabilities and usability

#### P2.1: Skill ecosystem compatibility and lifecycle
**Evidence:** PR #368 says current GitHub import deliberately installs one `SKILL.md` and does not copy companion resources/scripts. This is safer than executing repository content, but not full ecosystem parity.

**Required work**
- Add explicit multi-skill repository path selection, preview, pinned provenance, update/diff/rollback/remove, integrity checks and trust labels.
- Support workspace vs user vs shared installation scopes with clear precedence and isolation.
- Keep executable companion scripts disabled by default; if supported later, require sandboxing and explicit capabilities.
- Add compatibility tests for common Agent Skills frontmatter, path layouts, malformed metadata, symlinks and oversized content.

#### P2.2: Desktop is a shell around the guarded runtime, not yet a full companion UX
**Evidence:** `ui/desktop_app.py` launches the runtime as a child process. The perception module's capture is explicit and local-only; README states full vision, wake-word and desktop-control capabilities are incomplete or unverified.

**Required work**
- Improve structured task progress, cancellation, approvals, error recovery, model selection, logs, and a task timeline.
- Implement vision as an explicit opt-in pipeline: capture → local preview/consent → OCR/vision provider decision → bounded context → user-visible source/provenance. Never infer that permission to capture also means permission to upload.
- Implement desktop control only through a small typed action API with preview, scoped confirmation, timeout, cancellation and observed postconditions; avoid arbitrary mouse/keyboard scripts as the initial interface.
- Treat mood inference as opt-in and non-authoritative; do not use it for consequential decisions.
- Add accessibility, keyboard-only, screen-reader, and high-DPI testing.

#### P2.3: Voice and proactive behavior need lifecycle and quality gates
**Evidence:** voice/STT/TTS/wake-word modules and reminder/proactive modules exist, but module presence alone does not establish a reliable always-on companion.

**Required work**
- Measure speech recognition and synthesis quality on supported Windows hardware and supported languages.
- Make microphone activation visible, provide push-to-talk as the reliable default, and make wake-word mode opt-in.
- Add explicit stop/pause, device-loss recovery, offline operation, and no-recording-by-default tests.
- Put reminders, scheduled tasks and background evolution behind a persistent scheduler with clear status, cancellation and restart behavior.

#### P2.4: Release and upgrade engineering
**Required work**
- Add release signing and artifact provenance, SBOM, dependency pinning and reproducible-build checks where feasible.
- Test upgrades from prior releases while preserving user config, workspace, skill provenance and memory.
- Add automatic update only after signature verification, staged rollout, rollback and user controls exist.
- Publish support matrix, backup/restore guide, diagnostics bundle with redaction, and release notes.

## Comparison with established agent systems

This comparison is about documented product/runtime capabilities, not a claim that another system is universally better. Yuki has a different product goal: local-first Windows desktop companion plus CLI.

| Capability | Yuki at audited snapshot | Established reference pattern | Gap / recommendation |
|---|---|---|---|
| Long-running workflows | Durable task metadata exists; durable execution is not established end-to-end | LangGraph: checkpoints, durable execution, interrupt/resume, stateful workflows | Add checkpointed execution and crash-recovery tests |
| Tool execution | Bounded registered-skill calls with approval; no arbitrary shell in the core task loop | OpenHands: explicit agent runtime and sandboxed execution environments | Build a narrow, isolated execution backend before broad coding autonomy |
| Model/provider choice | Multiple adapters, but catalog/settings/runtime integration is split across stacked PRs | OpenClaw/OpenHands: provider/model-agnostic routing with runtime choices | One provider capability registry and adapter conformance suite |
| Skill ecosystem | Safe `SKILL.md` import; resources and scripts intentionally omitted | OpenClaw: skill scopes, loading precedence, updates, verification and install policy | Add lifecycle, scopes, provenance and compatibility without unsafe execution |
| Multi-agent | Bounded advisory specialist council and workflow workers | LangGraph/Deep Agents: subagent delegation plus state, context and runtime policies | Add isolation, cost budgets, evaluation and verifier; avoid “100 agents” as a raw count goal |
| Observability/evaluation | Task ledger and diagnostics; unified trace + benchmark suite not demonstrated | LangGraph/Deep Agents: inspectable state and production runtime; OpenTelemetry patterns in agent systems | Structured trace events and golden tasks before scaling |
| Screen/camera | Explicit local capture primitives; full visual assistant not implemented | OpenClaw documents paired device nodes for camera/screen and voice workflows | Build opt-in perception pipeline with explicit upload policy and tests |
| Security | Strong fail-closed and transport foundations in reviewed modules | OpenClaw documents sandboxing, tool policy and security audits; OWASP MCP guidance stresses least privilege and schema integrity | Add executable-tool sandbox, policy audit command, schema pinning and adversarial tests |

Reference docs:
- LangGraph overview and durable execution: https://docs.langchain.com/oss/python/langgraph/overview
- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- OpenHands: https://www.openhands.dev/product
- OpenClaw skills: https://docs.openclaw.ai/tools/skills
- OpenClaw sandboxing: https://docs.openclaw.ai/sandboxing
- OpenClaw capability model: https://docs.openclaw.ai/tools
- OWASP MCP Security Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html
- Claude Code CLI/permission modes: https://docs.anthropic.com/en/docs/claude-code/cli-usage

AutoGen note: Microsoft's AutoGen repository currently identifies itself as archived and no longer maintained. Treat it as historical design reference, not the default new dependency: https://github.com/microsoft/autogen

## Suggested 90-day roadmap

### Phase A — Stabilize and prove the core (weeks 1–2)
- Consolidate #364/#370 and dependent provider/model-selection work into one runtime.
- Finish exact-head CI and independent review.
- Execute Windows/Linux Ollama and desktop acceptance matrix.
- Publish a truthful support matrix for provider, tool, voice, perception and OS capabilities.

**Exit gate:** one install path works, one task can complete safely end-to-end, and failures are diagnosable.

### Phase B — Reliable task runtime (weeks 3–5)
- Typed tool schemas, explicit task state machine, durable checkpoints and pause/resume.
- Per-tool policy and approvals bound to exact action arguments.
- Structured traces, replay fixtures, cancellation, retry budgets and postcondition verification.
- Add golden task/evaluation harness.

**Exit gate:** repeatable task-success and safety metrics exist; crash/restart tests pass.

### Phase C — Provider and skill platform (weeks 6–8)
- One provider capability registry, adapter contract suite, local-first hybrid routing.
- Skill install/update/rollback/provenance lifecycle and per-scope trust policies.
- Restricted tool execution sandbox; MCP schema pinning and policy audit.

**Exit gate:** every visible provider/skill is labeled supported, experimental or unavailable; no silent fallback.

### Phase D — Companion intelligence (weeks 9–12)
- Separate semantic local memory from lexical fallback; memory inspect/correct/delete.
- Explicit opt-in vision pipeline and narrowly scoped desktop-control API.
- Voice and scheduler reliability, cancellation and device-loss recovery.
- Measure latency, cost, task success, privacy incidents and recovery.

**Exit gate:** demonstrate measurable improvements over Phase A on real tasks, not just more features.

## Release gate checklist

- [ ] Current exact-head CI is green on Windows and Linux.
- [ ] Independent code/security review completed.
- [ ] Real Ollama inference passes on supported operating systems.
- [ ] Installer upgrade, uninstall/reinstall, config persistence and rollback tested.
- [ ] No second desktop/provider execution path bypasses the guarded runtime.
- [ ] Approval, sandbox, cancellation, timeout, retry and postcondition tests pass.
- [ ] Prompt-injection, SSRF, secret-redaction and skill-install adversarial tests pass.
- [ ] Golden task evaluation and regression thresholds are published.
- [ ] Memory privacy controls and screen/camera permission flows are acceptance-tested.
- [ ] Documentation accurately labels implemented, experimental and planned capabilities.

## Final recommendation

Do not optimize for “100 AI agents” as a target. Build a small set of specialist roles on top of one durable, policy-enforced runtime, and add roles only when evaluation shows a measurable benefit. The competitive advantage should be **local-first privacy, transparent approvals, extensible skills, model choice, and a dependable desktop companion**—not the raw number of modules or agents.

This audit is source-based. It does not certify security, model quality, real inference, or end-user desktop behavior. Those require executable tests and real-device acceptance.
