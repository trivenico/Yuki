# Upstream architecture review: Odysseus and OpenMontage

**Review date:** 2026-11-04  
**Purpose:** Identify transferable architecture and product ideas for Yuki.  
**Scope:** Public repository source, documentation, root manifests and selected implementation files at the pinned revisions below. This is not a complete code audit, penetration test, or legal opinion.

## Sources reviewed

| Project | Revision reviewed | License | Focus |
|---|---|---|---|
| [odysseus-dev/odysseus](https://github.com/odysseus-dev/odysseus) | `dev` — `c21980f5a6d1cb84db8c8ab0f488cefc7764eb3f` | AGPL-3.0-or-later | Self-hosted AI workspace, auth, tools/MCP, research, model serving, skills, memory, email, docs, calendar |
| [calesthio/OpenMontage](https://github.com/calesthio/OpenMontage) | `main` — `d21f5d4eaa08422b09c7091757a05affcbabb35b` | AGPL-3.0 | Agent-driven creative production pipelines, tool contracts, schemas, checkpoints, approvals, quality gates, cost governance |
| Yuki | `feature/reasoning-context-builder` — `e5a5e2d576a5a351b3c30b50c249b8179bd2286b` | MIT | Existing architecture comparison |

Pinned upstream links:
- [Odysseus README](https://github.com/odysseus-dev/odysseus/blob/c21980f5a6d1cb84db8c8ab0f488cefc7764eb3f/README.md)
- [Odysseus SECURITY](https://github.com/odysseus-dev/odysseus/blob/c21980f5a6d1cb84db8c8ab0f488cefc7764eb3f/SECURITY.md)
- [Odysseus THREAT_MODEL](https://github.com/odysseus-dev/odysseus/blob/c21980f5a6d1cb84db8c8ab0f488cefc7764eb3f/THREAT_MODEL.md)
- [OpenMontage README](https://github.com/calesthio/OpenMontage/blob/d21f5d4eaa08422b09c7091757a05affcbabb35b/README.md)
- [OpenMontage architecture](https://github.com/calesthio/OpenMontage/blob/d21f5d4eaa08422b09c7091757a05affcbabb35b/docs/ARCHITECTURE.md)
- [OpenMontage agent contract](https://github.com/calesthio/OpenMontage/blob/d21f5d4eaa08422b09c7091757a05affcbabb35b/AGENT_GUIDE.md)

## Executive assessment

**Do not import either project wholesale.** Both are AGPL-3.0 projects while Yuki is MIT-licensed. Copying or adapting copyrightable implementation code into Yuki can create material license-compatibility and source-disclosure obligations, particularly for a network service. Obtain legal review and make an explicit licensing decision before copying any code. Keep this review to independently implemented architectural patterns unless that decision changes. Third-party assets, provider SDKs, skills, model weights and dependencies require their own provenance review.

The strongest opportunity is to combine:
- Odysseus' breadth of self-hosted workspace workflows and hardware-aware local-model onboarding.
- OpenMontage's explicit, schema-validated production pipeline, stage gates, decision history, provider scoring and cost controls.
- Yuki's existing permission-aware memory, durable workflow state, authenticated approvals, capability contracts, specialist worker coordination and local-first provider abstractions.

Yuki already has substantial equivalents to the central OpenMontage primitives. The next step is integration and end-to-end reliability, not another parallel registry or orchestrator.

## 1. Odysseus: what is useful

Odysseus describes itself as a self-hosted AI workspace spanning chat/agents, MCP and tools, local model serving, research, model comparison, documents, email, notes, tasks and calendar. Its source tree separates core auth/middleware/database primitives, route handlers, services and integrations.

### Patterns worth adopting

1. **Hardware-aware local-model onboarding (high priority).** Its Cookbook concept recommends models and serving profiles based on available hardware, quantization, VRAM/RAM, backend support and reliability. Yuki has a provider/model catalog and Hugging Face model discovery work, but needs an end-to-end hardware preflight and verified download/serve flow. Implement a read-only hardware inventory first; then deterministic compatibility scoring; then an explicitly approved download/start action. Never claim a model is usable merely because it appears in a catalog.
2. **Deep research as a durable workflow.** Gather sources, read them, preserve citations/provenance, synthesize a report, and persist resumable stage state. Yuki has bounded web reading and durable workflow primitives; compose them into a narrow research workflow with source-quality and injection boundaries rather than creating a second orchestration engine.
3. **Blind multi-model comparison.** Evaluate candidate providers/models on the same fixed prompt and deterministic rubric, hide provider identity during initial scoring, then reveal and synthesize. Use Yuki's existing model evaluation and provider catalog. Record model/version, prompt, settings, failures and cost so results are reproducible.
4. **Degraded-state visibility.** Distinguish configured, installed, authenticated, reachable, healthy and feature-capable. Provider and integration readiness should be explicit and sanitized; never expose credentials. This is particularly important for Ollama and optional cloud adapters.
5. **Workspace workflows.** Email, documents, notes, tasks and calendar are valuable product surfaces, but should be delivered incrementally behind narrow scopes, per-action approval and actor-attributed audit. Do not interpret an admin-console design as a safe default for a personal assistant that can act autonomously.

### Security lessons and caution

Odysseus explicitly frames itself as a trusted-user/private-network admin console. Its threat model documents powerful admin shell/filesystem/email/MCP capabilities, no shell/filesystem sandbox, a prompt-injection approval gate with deliberate exemptions and bypass controls, coarse token scopes, and an SSRF issue involving a chat-scoped `base_url` (the document points to a fixing PR). Its security policy recommends authentication, HTTPS, private access layers, internal-only service ports, protected data directories, and admin-only high-risk tools.

These are useful threat scenarios, **not** reasons to copy its controls. Yuki should retain deny-by-default execution, per-action authorization, request-scoped identity, explicit approvals, and isolated execution for untrusted jobs. In particular:
- Never let an agent-provided provider base URL bypass Yuki's SSRF and endpoint policy.
- Do not give an agent a broad admin loopback identity to simplify tool access.
- Do not let a single broad "approval" grant unrelated send/delete/shell capabilities.
- Do not allow prompt-injection mitigations to be disabled silently or bypassed by content-source labels.
- Keep shell and filesystem access out of the default web profile.

Odysseus' roadmap also candidly identifies fresh-install portability, integration reliability, model-cookbook compatibility, prompt/context bloat, skill/tool prompt-injection testing, degraded-state reporting and backup/restore as unfinished work. These are good signals for Yuki's own release gates: breadth is not the same as proven integration quality.

## 2. OpenMontage: what is useful

OpenMontage uses an agent-driven pipeline model: an agent reads a YAML pipeline manifest, follows stage-specific Markdown instructions, calls registered Python tools, validates canonical artifacts, writes checkpoints, reviews output, and pauses for human approval where required. It documents a layered knowledge model (tools/pipeline definitions, project-specific skills, external technology knowledge packs).

### Patterns worth adopting

1. **Typed capability/tool contracts.** A tool describes identity/version, capability, provider, runtime class, stability, dependencies, input/output schema, fallback options, resource profile and retry policy. This makes discovery and planning grounded in what is actually available.
2. **Schema-validated workflow manifests.** Declare ordered stages, outputs, permitted tools, review criteria, success criteria, retry/revision limits and approval defaults. Keep manifests declarative and validate them before execution.
3. **Canonical artifacts and checkpoints.** Each stage produces a typed artifact; a checkpoint records stage, status, artifacts, approvals and provenance. Validate that a stage cannot be marked complete without its required artifact.
4. **Human approval as an enforced transition.** An approval gate should be a state-machine invariant, not merely an instruction in a prompt. Preserve the approver, approved payload digest, timestamp and supersession history.
5. **Append-only decision trail.** Record provider/model/tool choice, alternatives, reason, confidence, fallback and explicit user changes. Do not silently rewrite a previous decision when the plan changes.
6. **Cost governance.** Estimate, reserve, reconcile actual spend; support observe/warn/cap modes and approval thresholds. Apply this to any paid inference, generation, messaging or external action, not just creative media.
7. **Preflight and post-action quality gates.** Check tool availability, dependencies, input validity, output integrity and declared delivery criteria before declaring success. A provider returning HTTP 200 is not proof that the artifact is usable.
8. **Resource-aware tool selection.** CPU/RAM/VRAM/disk/network requirements and local/API/hybrid runtime metadata can make model and tool routing more reliable.

### Architectural differences to preserve

OpenMontage explicitly has **no runtime Python orchestrator**: the IDE agent is the control plane and Python supplies tools and persistence. That is a useful authoring model for coding-assistant workflows, but it should not replace Yuki's durable runtime orchestration. Yuki needs server-side bounded execution, identity propagation, crash recovery, idempotency, durable approvals and enforceable permission checks. Use pipeline manifests as declarative plans consumed by Yuki's existing workflow engine, not as a second authority layer.

The OpenMontage pipeline loader uses `yaml.safe_load` plus JSON Schema validation, which is a good pattern. Its tool base class also loads a project `.env` into process environment at import time; Yuki should **not** adopt that import-time secret-loading side effect. Load secrets through an explicit, scoped configuration boundary. Review all tool subprocess, path, network, retry and logging behavior individually before any reuse.

## 3. Existing Yuki overlap — avoid duplicating these systems

Yuki already has the following foundations at the reviewed commit:

| Upstream concept | Existing Yuki implementation | Decision |
|---|---|---|
| Capability/tool inventory and readiness | `core/capability_manifest.py`, `core/capabilities.py`, `skills/registry.py` | Extend contracts with runtime/resource/dependency metadata only where needed; don't create another registry |
| Provider catalog and capability routing | `providers/catalog.py`, `providers/capabilities.py`, `core/model_capabilities.py` | Add measured health and hardware fit; keep privacy policy and explicit cloud opt-in authoritative |
| Durable stage/workflow state | `automation/workflow_engine.py`, `automation/workflow_store.py`, `automation/workflow_approval.py` | Compose declarative workflow definitions with existing durable engine |
| Specialist agents and task coordination | `core/intelligence_orchestrator.py`, `automation/engineering_workflow.py`, worker/coordinator modules | Add workflow templates and proven task contracts; don't inflate agent count as a goal |
| Human-approved workspace changes | Phase 92/93 approval and change/test runners | Preserve digest-bound approval; test runner is not a sandbox |
| Research/read tools | bounded web skills and authorized read-tool integration | Compose into cited, resumable research workflow with source provenance |
| Backup scripts | `scripts/daily_backup.sh`, `scripts/daily_backup.bat` | Still needs encryption, DB-consistent snapshots, and verified restore; archives are not encrypted |
| Skill portability/provenance | `core/skill_packs.py`, `docs/UPSTREAM_CAPABILITY_AUDIT.md` | Continue quarantine, metadata validation and no automatic execution |

Yuki's capability manifest deliberately distinguishes implemented, experimental and planned status. Keep that conservative standard. A new upstream-inspired feature must have a named end-to-end test before being labelled implemented.

## 4. Recommended implementation roadmap

### P0 — close safety/recovery before adding new power

- Finish encrypted, authenticated, database-consistent backup and clean-workspace restore; wrong-key, tamper, corruption and interrupted-restore tests.
- Complete tenant isolation, network-aware rate limiting, SSRF/DNS-rebinding audit, tool authority propagation and independent threat review.
- Add durable, actor-attributed audit records for workflow approvals and consequential tool actions.
- Do not add shell, email-send, publish or arbitrary MCP execution to default profiles.

### P1 — highest product leverage

1. **Local Model Readiness & Hardware Advisor (first feature).**
   - Read-only hardware inventory: OS/architecture, CPU, RAM, GPU, VRAM, driver/runtime and disk availability.
   - Model metadata: parameter size, quantization, context length, modalities, license, format, backend support and source revision.
   - Deterministic fit score with explicit hard constraints and confidence/unknown values.
   - Explain why a model fits or does not fit; never invent VRAM or benchmark values.
   - Separate states: discovered → compatible → downloaded → installed → serving → health-checked.
   - Require user approval before downloads, package installs, server launch or cloud use.
   - Test with synthetic hardware fixtures on Linux, Windows and macOS; no network in unit tests.
2. **Resumable Deep Research workflow.**
   - Manifest stages: clarify question → search → fetch/read → evidence extraction → synthesis → citation validation → final report.
   - Store source URL, retrieval time, excerpt/hash, claims supported and failure status.
   - Keep web content as untrusted input; citations must map to actual retrieved sources.
   - Resume safely after crashes; no false "complete" report after a source-read failure.
3. **Model comparison/evaluation workspace.**
   - Same task/prompt and fixed evaluation rubric; blind first-pass scoring.
   - Track latency distribution, token usage when available, failure rate, tool correctness and estimated/actual cost.
   - Persist model IDs, versions, quantization, parameters and benchmark dataset version.
   - Avoid declaring a universal "best model"; report task-specific trade-offs.
4. **Workflow manifest + artifact contracts.**
   - Add a small typed schema that maps declarative stages to existing Yuki workflow handlers.
   - Each stage declares input/output schemas, allowed capability IDs, risk, side effects, timeout, retry/idempotency policy, approval requirement and acceptance checks.
   - Validate before execution; runtime independently enforces actor permissions and approval.
   - Keep decisions and approvals append-only and digest-bound.

### P2 — after P1 is proven

- Scoped notes/tasks/calendar integrations with per-action permissions and durable audit.
- Email read-only triage first; draft replies next; send/delete only with explicit per-message confirmation.
- Creative media pipelines (transcribe, summarize, clip, caption, compose) as optional specialist workflows. Keep all generation providers optional; prefer local FFmpeg/Whisper/Piper where supported and require consent for paid cloud providers.
- Multi-device companion gateways only after pairing, token scopes, revocation, offline queues and tenant tests are complete.

## 5. Acceptance criteria for the first feature

The Local Model Readiness & Hardware Advisor is recommended as the first upstream-inspired product feature because it strengthens Yuki's local-first promise without granting new action authority.

- Inventory collection is read-only and can be disabled.
- No shell command is constructed from model output; system probes use fixed allow-listed executables/arguments.
- Missing hardware fields remain unknown; no fabricated capability estimates.
- Model metadata is pinned to source, revision, license and retrieval time.
- Compatibility output is deterministic for a fixed fixture and policy version.
- Download/install/serve are separate actions with explicit user approval and bounded resource budgets.
- Provider health is actively probed only after consent and with SSRF-safe endpoint validation.
- Tests cover low RAM/VRAM, integrated GPU, unavailable driver, unsupported quantization, offline mode, malformed metadata and cloud opt-in denial.
- Feature status remains experimental until supported-platform end-to-end tests pass.

## 6. Explicit non-goals

- Do not copy AGPL source into the MIT Yuki repository without a licensing decision.
- Do not adopt OpenMontage's IDE-agent-as-runtime-control-plane for Yuki's production runtime.
- Do not copy Odysseus' broad admin-console trust assumptions.
- Do not expose internal Ollama, database, MCP or model-serving ports publicly.
- Do not equate 100 registered agents or skills with 100 reliable capabilities.
- Do not promote a feature based on a README claim, star count, module count or provider catalog entry alone.

## Review conclusion

**Adopt the patterns, not the code.** Yuki's current advantage is its permission-aware memory and durable workflow foundations. The highest-value next feature from these reviews is a hardware-aware local model readiness advisor, followed by a cited resumable research workflow and a reproducible blind model-evaluation surface. Complete P0 security and recovery work in parallel; do not expand privileged tool access to chase feature breadth.
