# Yuki competitive capability benchmark

**Snapshot:** 25 November 2026  
**Purpose:** Compare Yuki's verified repository capabilities with adjacent open-source assistants and agent frameworks. This is a product/engineering gap analysis, not a model leaderboard or security certification.

## Executive assessment

Yuki is currently a **restricted developer preview with substantial security and architecture work in progress**. It is not yet at feature parity with OpenClaw or Hermes Agent as a daily-use assistant, and it does not yet match OpenJarvis's local-inference/evaluation focus. Its strongest current investment is in explicit permission boundaries, durable task/security repositories, provider-independent core architecture, and PostgreSQL role isolation.

The repository's release-readiness document estimates the intended personal-assistant MVP at approximately 40% (20 November 2026). This is a subjective engineering estimate, not a benchmark score. The README explicitly says Yuki is not production-ready and does not yet contain a trained foundation model or a production-grade native tool-calling/ReAct runtime.

## Category distinctions

These projects are not all the same kind of product:

- **OpenClaw** is a self-hosted personal assistant and gateway that connects chat channels, devices, tools and selectable model providers.
- **Hermes Agent** is an end-user agent runtime with persistent memory, skills, subagents, messaging gateways and a learning loop. It is model-agnostic.
- **OpenJarvis** is a local-first personal-AI research/framework stack emphasizing on-device inference, interchangeable engines/agents/tools/memory, and efficiency-aware evaluation/learning.
- **OpenHands** is primarily an autonomous software-engineering agent and execution environment, not a general personal assistant.
- **LangGraph / CrewAI / Microsoft Agent Framework** are developer frameworks for building stateful or multi-agent systems, not turnkey personal assistants.
- **Yuki** is intended to become a personal assistant and controlled agent platform with a secure core, local-first workflows, optional model adapters, and eventually specialist agents. That full product is still being built.

## Capability comparison

| Dimension | Yuki (verified current state) | OpenClaw | Hermes Agent | OpenJarvis | OpenHands |
|---|---|---|---|---|---|
| Ready-to-use assistant UX | Restricted Python CLI/developer preview; desktop and voice pieces are incomplete | Mature gateway, CLI/UI and many messaging/device channels | CLI/Desktop and 20+ messaging integrations | CLI and local GUI; personal-device workflows | Mature coding-agent UX and web/CLI workflows |
| Model strategy | Provider-neutral architecture direction; provider scaffolding exists, but no production-grade native tool loop or trained Yuki foundation model | Model/provider agnostic; extensive provider catalog | Model/provider agnostic; Nous Portal, OpenRouter, OpenAI and custom endpoints | Local model catalog and pluggable inference engines; cloud optional | Multiple model providers, configured for coding-agent tasks |
| Own foundation model | No | No; model providers are external | Agent is separate from Nous model families; Hermes models are optional choices, not the agent itself | No single OpenJarvis foundation model; it runs selectable models | No |
| Memory | Local approved notes/lexical search and task persistence; no automatic dream cycle | Persistent assistant state and memory features | Strong persistent memory, session search and self-improving skills | Local memory and trace-driven learning research | Workspace/task context and agent memory patterns |
| Skills/tools | Guarded skill registry; approval per registered invocation; MCP primitive not yet fully wired | Broad tools, skills, plugins and community ecosystem | Skills, MCP, tool runtime and plugin ecosystem | Skills catalog/import, connectors and built-in agents | Strong coding tools, shell/browser and sandboxed execution patterns |
| Multi-agent/delegation | Durable workflow/delegation/security primitives under active PostgreSQL role-isolation testing; not yet a broad autonomous specialist fleet | Agent sessions and extensible gateway capabilities | Isolated subagents, parallel work and persistent specialist bots | Eight built-in agents across on-demand, scheduled and continuous modes | Delegated coding workflows and specialized agents |
| Scheduling/always-on | Workflow scaffolding exists; full production scheduler/recovery is not complete | Gateway and channel services designed for always-on use | Built-in cron and persistent server deployment | Scheduled and continuous agents | Long-running coding tasks; not a general personal scheduler |
| Local-first operation | Local CLI/data foundations; Linux VPS pilot planned, local model inference still to validate | Can self-host on personal hardware; hosted or local models | Can run on VPS/local/container backends | Core design focus: on-device inference and efficiency | Usually a controlled execution environment; not specifically personal-device-first |
| Security posture | Significant work on least privilege, approvals, audit, PostgreSQL service identities and isolation; independent review and sandbox validation still missing | Security guidance and pairing; host tools require deliberate sandboxing/configuration | Command approval, authorization and container isolation features | Research/engineering framework; validate its deployment security separately | Sandboxed execution is a central design feature; still requires deployment hardening |
| Evaluation | Evaluation adapters and test foundations exist; real provider/task quality baselines incomplete | Operational model/provider selection; not directly comparable as an evaluation research platform | Trajectory generation/compression and training workflows | Explicit efficiency/latency/energy/cost evaluation and learning loop | Coding-agent benchmarks and task evaluation |
| Production evidence for this comparison | No Yuki release or private pilot yet | Established public project and active ecosystem | Established public project and active ecosystem | Public research framework with 1.0 architecture | Established coding-agent project |

**Interpretation:** OpenClaw and Hermes are ahead in day-to-day assistant experience and integration breadth. OpenJarvis is ahead in local inference and efficiency-oriented evaluation. OpenHands is ahead in coding-agent execution maturity. Yuki is not ahead overall today; its potential differentiation is a deliberately controlled personal-agent core and a rigorous security/permission model, but that advantage must be proven through working end-to-end product behavior and independent testing.

## The model question: what is Yuki's AI?

Yuki currently does **not** have its own trained foundation model. The product architecture intentionally separates:

1. **Yuki platform intelligence:** deterministic intent handling, task state, policy-aware routing, retrieval, validation, approvals, audit and recovery.
2. **Model adapters:** optional external or local LLMs used for language generation, summarization and model-assisted planning.
3. **Execution authority:** Yuki's own policy/runtime layer. A model proposes; Yuki authorizes and executes only registered, approved actions.

This is a deliberate separation. It avoids making model output the security authority and allows provider changes without rewriting core state or permission logic.

### How competitor model names should be understood

- **OpenClaw:** the project is not one model. It can route to external hosted or local models through provider adapters. Its model choice is configured separately from the gateway and tools.
- **Hermes Agent:** the agent runtime is not the same thing as a Hermes model. Nous Research publishes both the agent and separate model families (for example Hermes 4 variants); the runtime can also use other providers.
- **OpenJarvis:** the framework is not one foundation model. It manages a model catalog and pluggable inference engines, with local-first model selection and efficiency measurements.
- **OpenHands:** the coding agent uses configurable models; its value is the software-engineering loop and execution environment, not a proprietary OpenHands foundation model.
- **Yuki:** no Yuki-branded foundation model exists today. We should not train one merely for branding. First prove the agent runtime with a strong existing model, then assess whether a fine-tuned specialist or smaller local model produces measurable benefit.

## Where Yuki is actually progressing

The repository evidence as of this snapshot:

- The README describes bounded, approval-gated skill invocation, local memory, planning proposals, selected integrations, defensive security foundations, and an explicit list of incomplete capabilities.
- The native-intelligence architecture says core workflows should continue when model credentials are absent, with no silent cloud fallback.
- The release-readiness estimate (20 November) is approximately 40% of the intended personal-assistant MVP, with provider experience, security/isolated execution, desktop/voice and operations still incomplete.
- The current engineering series adds real PostgreSQL LOGIN-role tests for the API conversation store, workload delegation, workflow store, authorization-audit writer and migration executor. These tests are authored and awaiting CI confirmation; they are not proof of passing tests yet.
- The migration-executor work uses a separate disposable database and distinct LOGIN identities. No production roles, grants, secrets or cloud resources have been provisioned.

## Recommended benchmark strategy

We should compare actual task outcomes, not count features, stars, agents or source lines. Build a reproducible suite with the same model tier, task prompts, tool permissions, machine limits and timeout for every system.

### Track A — assistant usefulness
- Multi-turn task completion and instruction adherence
- Memory recall precision/recall and user-controlled deletion/export
- Research accuracy, citation correctness and uncertainty handling
- Multilingual response quality and voice latency

### Track B — agent/tool reliability
- Tool-selection accuracy and schema-valid calls
- End-to-end success on file, browser, coding and workflow tasks
- Recovery after process restart, timeout, cancellation and duplicate delivery
- Human approval correctness; zero unauthorized side effects

### Track C — security and resilience
- Prompt-injection resistance across web pages, files, skills and tool output
- Permission escalation and cross-tenant/data-boundary tests
- Sandbox escape and network-egress tests
- Secret leakage, audit completeness, rollback and restore drills

### Track D — local efficiency
- Model/task quality at fixed hardware and quantization
- p50/p95 latency, tokens/sec, RAM/VRAM, energy and cost per successful task
- Offline operation and degraded-mode correctness

Report each track separately. Do not collapse safety, answer quality, latency and cost into one opaque score. Record exact commit, model ID/revision, quantization, prompts, datasets, hardware, configuration and artifact digests.

## Priority roadmap from this comparison

1. **Finish and verify the secure foundation:** resolve the stacked PostgreSQL isolation PRs, obtain green CI, consolidate role/privilege verification, and complete independent review. No production grant script before exact grants are tested.
2. **Deliver one real model-backed assistant loop:** provider selection, streaming, cancellation, tool schemas, bounded planning, approval, execution, result validation and audit. Use one hosted provider initially; keep the provider interface portable.
3. **Prove one complete user workflow:** e.g. research a user-approved folder, cite sources, create a draft artifact, ask before external side effects, and recover cleanly after restart.
4. **Add local inference as a measured option:** test a small capable model on target hardware/VPS; do not assume the Oracle VPS has a GPU. Keep local and hosted models on the same task and safety evaluations.
5. **Build specialist agents only when they have real, tested ownership boundaries:** each agent gets explicit input/output contracts, allowed tools, budget/time limits, state ownership, approval requirements and failure recovery. Scale from a few proven agents toward the 100-agent roadmap.
6. **Run the private Linux VPS pilot:** only after the private deployment, secrets, database roles, monitoring, backup/restore and rollback gates are satisfied. Windows compatibility follows the shared core after Linux pilot evidence.

## Sources checked

- [Yuki repository](https://github.com/trivenico/Yuki), [release readiness](RELEASE_READINESS.md), [native intelligence architecture](NATIVE_INTELLIGENCE_ARCHITECTURE.md), and [upstream capability audit](UPSTREAM_CAPABILITY_AUDIT.md).
- [OpenClaw repository](https://github.com/openclaw/openclaw), [architecture/security overview](https://docs.openclaw.ai/), and [model provider directory](https://docs.openclaw.ai/providers).
- [Hermes Agent repository](https://github.com/NousResearch/hermes-agent), [official documentation](https://hermes-agent.nousresearch.com/docs), and [Nous Research model releases](https://nousresearch.com/releases).
- [OpenJarvis repository](https://github.com/open-jarvis/OpenJarvis), [architecture overview](https://github.com/open-jarvis/OpenJarvis/blob/main/docs/architecture/overview.md), and [1.0 changelog](https://github.com/open-jarvis/OpenJarvis/blob/main/CHANGELOG.md).
- [OpenHands repository](https://github.com/All-Hands-AI/OpenHands).
- [LangGraph, CrewAI and Microsoft Agent Framework comparison context](https://www.langchain.com/resources/ai-agent-frameworks).

Public project feature descriptions are based on their documentation/repositories as accessed on 25 November 2026. Features and releases evolve; re-check before a purchasing or deployment decision. This document does not assert that every competitor feature has been independently tested.
