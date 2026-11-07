# AI-agent ecosystem gap analysis — 2026-11-05

**Scope:** source-backed review of Yuki's current branch plus public documentation for leading agent runtimes and adjacent projects. This is an architecture gap analysis, not a claim that Yuki has feature parity with any platform. A module or test file is not evidence of an end-to-end user capability.

**Audited branch:** `feature/reasoning-context-builder`  
**Audited head before this document:** `04cbdc6fa27c5c6c2c5f9292a183bd5f8e56c585`  
**Readiness:** not production-ready; see [PRODUCTION_READINESS_CURRENT.md](PRODUCTION_READINESS_CURRENT.md).

## Executive decision

Yuki should remain a local-first product with its own owner-scoped memory, policy, model routing, approval, and audit boundaries. It should **not** attempt to reimplement every framework feature or adopt a large framework wholesale.

The competitive gap is primarily integration and productization:

1. Connect the bounded reasoning loop, specialist coordinator, worker runtime, memory, model router, and tool protocol into one observable end-to-end agent runtime.
2. Add durable run/step state, resumability, and human interrupts with explicit rules for side effects.
3. Make model/tool capabilities measurable and route by task quality, hardware, privacy, latency, and cost.
4. Make MCP and skills real, governed integration surfaces rather than disconnected adapters/catalogues.
5. Productize persistent per-agent workspaces, scheduling, channels, and operator controls only after isolation and audit gates are closed.

A target of 100 agents should mean a catalog of tested, capability-scoped specialist definitions—not 100 independently running autonomous processes. Start with a small number of useful specialists and prove routing, delegation, review, and recovery.

## Yuki capability inventory (verified from source/docs)

| Area | Current evidence | Actual boundary / gap |
|---|---|---|
| Model providers | Ollama, OpenAI-compatible, Gemini CLI, hybrid/provider adapters and catalog | Provider availability depends on local runtime/credentials. Per-provider live failure and capability conformance coverage remains incomplete. |
| Model selection/evaluation | Model registry, capability metadata, hardware snapshot, Ollama inventory, evaluation modules | Need reproducible task-specific benchmark corpus and measured quality/latency/memory/cost/energy routing. Discovery metadata is not proof of model quality. |
| Agent loop | `core/agent_execution_loop.py`, structured tool protocol, reasoning adapters | Bounded synchronous loop; provider adapter must enforce network timeouts. README still describes the main task loop as bounded JSON model-guided execution, not a fully mature native tool-calling/ReAct runtime. Verify the actual composition path before calling it end-to-end. |
| Specialist agents | Static capability registry, DAG planner/coordinator, model-proposed bounded plans | Coordinator validates and persists plans; it is not a general dynamic agent loader or a fully autonomous agent team runtime. Plans are bounded (20 subtasks in coordinator; reasoning planner has its own smaller cap). |
| Durable workflows | SQLite task/workflow state, leases, approvals, operation journal, audit events | No general distributed scheduler or exactly-once external side effects. SQLite is not a distributed queue. Provider timeout and reconciliation semantics still need adapter-level enforcement and crash/race tests. |
| Memory | Owner-scoped semantic memory, evidence-aware retrieval, multi-hop, candidate approval, lifecycle and correction history | Need end-to-end memory quality/forgetting/retention evaluation under real use, plus safe agent-specific memory UX and export/delete/backup guarantees. |
| MCP | Restricted HTTPS Streamable HTTP client, host allowlist, tool allowlist and approval hook | Current client explicitly excludes stdio; repository audit says it is not yet wired to runtime commands. No broad protocol/server interoperability or governed MCP registry demonstrated. |
| Skills/plugins | Guarded skill catalog and approval-oriented adapters | Third-party skills remain a supply-chain and prompt-injection boundary. Need signed/pinned provenance, license/dependency checks, sandboxed conformance tests, revocation and rollback. |
| Sandbox/computer use | Docker-backed bounded coding runner and sandbox policy | Not a hardened multi-tenant isolation boundary, persistent computer, or full desktop/browser automation platform. |
| Scheduling/background work | Worker supervision and bounded workflow execution | No productized durable cron/event scheduler with restart recovery, timezone semantics, missed-run policy, per-job approvals and operator history. |
| Channels/devices | Authenticated local web chat and device-agent foundations | No broad, production-tested messaging-channel gateway or seamless multi-device persistent agent identity. |
| Observability | Workflow/task events and retrieval diagnostics | Need unified run/step/tool/model traces, redaction, correlation IDs, metrics, cost accounting, replayable evaluations and operator dashboards. |
| Voice | STT/TTS/wake-word modules | Hardware/model-specific end-to-end validation, interruption/barge-in and privacy defaults remain open gates. |
| Deployment | Cross-platform CI and local MVP | No public multi-tenant production deployment, proven restore drill, independent security review or hardened untrusted-code isolation. |

## External ecosystem comparison

The comparison focuses on publicly documented architecture, not vendor marketing claims or unverified security assurances.

| Project / family | Strong documented pattern | What Yuki should learn | Yuki gap / decision |
|---|---|---|---|
| LangGraph / LangChain | Explicit graph or functional workflows, durable checkpoints, stores, human-in-the-loop and fault-tolerant execution | Durable typed run state, interrupt/resume semantics, checkpoint versioning and deterministic orchestration | Yuki has workflow persistence and approvals, but needs a unified agent-run graph/checkpoint contract and end-to-end resume tests. Reuse concepts; do not replace Yuki's authorization boundary. |
| CrewAI | Role-based agents, tasks, crews and event-driven Flows with state/guardrails | Clear specialist role contracts, structured outputs, sequential/hierarchical flows | Yuki's specialist registry and DAG are a good base; missing polished crew-level execution, review UX, tracing and measured specialist quality. |
| Microsoft Agent Framework / AutoGen lineage | Conversational/event-driven multi-agent coordination and graph workflows; Microsoft describes Agent Framework as the successor direction | Message/event contracts, agent-to-agent protocols, cancellation and team runtime | Yuki currently emphasizes deterministic DAG delegation, not rich agent conversations or distributed message runtime. Add only where collaboration benefits are measured. |
| Letta | Persistent agent identity with its own model/tools/memory/history; conversations share agent state; memory blocks and recall | First-class agent identity, scoped memory, context compilation/compaction, long-lived experience | Yuki has strong owner memory and retrieval primitives, but not yet a polished persistent-agent lifecycle and portable cross-device identity model. Preserve owner isolation; do not blindly share all memory between agents. |
| OpenClaw | Local-first gateway, stateful sessions, model/provider routing and failover, many messaging surfaces, voice and device nodes | Channel adapter architecture, gateway/session lifecycle, pairing and per-agent routing | Yuki lacks a production channel gateway, broad channel integrations, always-on gateway operations and proven multi-device UX. Shared gateway must remain a single trust domain unless tenant isolation is proven. |
| OpenHands | Agent runtime/control plane, local/cloud/VPC runtimes, integrations, task workflows from signal through plan/execute/validate/ship/monitor | Persistent isolated workspaces, software engineering workflows, CI/PR integrations and operator visibility | Yuki has coding-task execution and engineering workflow foundations, but not productized persistent per-agent VMs, secure lifecycle APIs, broad integrations or mature operational control plane. |
| OpenJarvis / local-first stacks | Separate model, inference engine, agent, tool, memory and learning layers; local inference and resource-aware evaluation | Strict component contracts, hardware-aware routing, trace-driven evaluation and offline operation | Yuki has provider/model/memory modules, but needs an end-to-end measured local-first benchmark and clean-machine offline acceptance suite. |
| MCP ecosystem | Standardized tools/resources/prompts, version negotiation, authorization and client/server protocol features | Protocol conformance, capability discovery, explicit auth scopes and interoperable integrations | Yuki's restricted HTTP client is a starting point, not broad MCP compatibility; runtime wiring, server management, protocol version support and transport strategy remain gaps. |

## Priority roadmap

### P0 — complete the common agent runtime

- Define a versioned `AgentRun`, `AgentStep`, `ToolInvocation`, `ModelInvocation`, `ApprovalRequest`, and `AgentArtifact` contract.
- Make the model/tool loop consume typed run state and emit redacted, correlated events.
- Persist checkpoints before and after every consequential boundary; never replay an uncertain external side effect.
- Add explicit interrupt/resume, cancellation, deadline, token/context budget, and partial-result semantics.
- Bind owner/principal, agent identity, task, workspace, tool, provider and approval snapshot to each invocation.
- Add integration tests from authenticated user request through model, specialist routing, worker, tool, approval, journal and final response.
- Resolve provider timeout semantics at adapter boundary. A Python thread timeout is not cancellation of a remote request; adapters must use transport-level deadlines and preserve uncertain outcomes.

### P1 — model intelligence and evaluation

- Build a versioned benchmark set from representative Yuki tasks: coding, research, planning, memory recall, tool selection, structured extraction, multilingual chat and safety refusals.
- Measure task success, groundedness, tool correctness, schema validity, latency p50/p95, tokens, RAM/VRAM, energy where measurable, and monetary cost where applicable.
- Run local Ollama and cloud provider suites independently; include offline, timeout, rate-limit, malformed output and auth-failure cases.
- Route by task capability and measured quality, not model popularity or raw parameter count.
- Support fallback only when privacy policy permits it; cloud fallback must be explicit and visible.
- Keep model download/install and license/weight provenance separate from metadata discovery.

### P1 — agent memory and identity

- Define whether memory belongs to owner, agent, project, conversation or shared team; require explicit grants for cross-scope retrieval.
- Add agent identity lifecycle, agent-specific prompt/config versioning, memory export/import and safe archival.
- Implement bounded context compaction with provenance-preserving summaries; never let retrieved content become system policy.
- Evaluate memory precision/recall, stale facts, correction propagation, contradictions, duplicates and deletion propagation.

### P1 — integrations and action surfaces

- Complete MCP runtime wiring with protocol conformance tests, explicit server trust, per-tool authorization, timeouts, result limits, audit and revocation.
- Add a signed/pinned skill manifest: publisher, immutable source revision, license, dependency hashes, requested permissions, test results and revocation status.
- Build one secure channel integration first; validate account pairing, sender authorization, rate limits, attachment scanning and session isolation before expanding.
- Keep browser/computer use in a separately isolated runtime with explicit approval for side effects.

### P2 — product and platform

- Persistent per-agent workspace lifecycle only after hardened isolation: provision, pause, resume, destroy, snapshot, resource limits, network egress policy and secrets.
- Durable scheduler with timezone-aware schedules, idempotent run IDs, missed-run policy, jitter, concurrency caps, pause/disable and audit.
- Unified observability: trace viewer, run timeline, model/provider costs, token/context budgets, worker health, queue lag and alerting.
- Voice and mobile/device continuity after real-device E2E and privacy review.

## Recommended architecture choice

Do not adopt LangGraph, CrewAI, AutoGen, Letta or OpenClaw as the Yuki core wholesale. Each optimizes a different abstraction and can pull Yuki toward a framework-specific state or trust model.

Instead:
- Keep Yuki's owner identity, authorization, memory policy, durable journal and provider boundary authoritative.
- Borrow graph/checkpoint patterns from LangGraph.
- Borrow explicit specialist role/task contracts from CrewAI.
- Borrow event/message patterns from AutoGen where asynchronous collaboration is justified.
- Borrow persistent agent identity and memory-block concepts from Letta.
- Borrow local gateway, channel and device adapter concepts from OpenClaw.
- Borrow isolated engineering runtime and CI workflow patterns from OpenHands.
- Borrow modular local inference and evaluation patterns from OpenJarvis.
- Implement MCP as an interoperability layer, not as a trust or permission system.

Any third-party source-code adoption requires independent license, provenance, dependency and security review. Architectural inspiration does not authorize copying implementation code.

## Acceptance gates (not feature-count targets)

1. One owner request can execute a bounded multi-step specialist workflow end-to-end, with typed artifacts and full trace.
2. Every tool action has authenticated principal, agent identity, permission decision, approval where required, and durable audit.
3. A crash at every tested boundary resumes safely without duplicating external side effects.
4. A model can be swapped between supported local/cloud providers without changing authorization or workflow semantics.
5. Local-only mode passes clean-machine offline tests after model provisioning.
6. At least three specialist workflows have task-specific evaluation baselines and regression gates.
7. MCP and imported skills pass explicit trust, permission, provenance and revocation tests.
8. Public multi-user release remains blocked until P0 security, isolation, backup/restore and independent review gates are closed.

## Primary references

- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- LangChain/LangGraph architecture overview: https://www.langchain.com/oss-overview
- CrewAI documentation: https://docs.crewai.com/
- Microsoft Agent Framework: https://learn.microsoft.com/en-us/agent-framework/
- Letta stateful agents: https://docs.letta.com/concepts/stateful-agents
- OpenClaw overview: https://docs.openclaw.ai/help/faq/what-is-openclaw
- OpenHands platform: https://www.openhands.dev/
- MCP specification: https://modelcontextprotocol.io/specification/
- OpenJarvis: https://github.com/open-jarvis/OpenJarvis
- Existing Yuki external capability audit: [EXTERNAL_AGENT_CAPABILITY_AUDIT.md](EXTERNAL_AGENT_CAPABILITY_AUDIT.md)
- Existing Yuki readiness gates: [PRODUCTION_READINESS_CURRENT.md](PRODUCTION_READINESS_CURRENT.md)
