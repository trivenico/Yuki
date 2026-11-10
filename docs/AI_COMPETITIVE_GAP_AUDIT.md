# Yuki AI Competitive and Capability Gap Audit

**Audit date:** 2026-11-10  
**Scope:** repository evidence reviewed on `main`, plus the active central-policy/approval dependency stack. This is an engineering gap analysis, not a benchmark claiming Yuki is better than commercial models.

## Executive assessment

Yuki has useful foundations: provider-backed chat, a bounded model/tool loop, structured tool contracts, durable task/worker primitives, specialist-agent coordination, local memory, an authenticated web-chat MVP, and an emerging default-deny policy/approval system.

It is not yet comparable to mature coding agents or production agent platforms in end-to-end capability. The largest gaps are not the number of named agents. They are runtime integration, task-aware model routing, robust retrieval, multimodal orchestration, systematic evaluation, traceability, and a coherent production application composition.

The README accurately warns that the repository is not production-ready and that several modules are not wired into the default application. Keep this honesty.

## Competitive reference set

| Reference | Strong pattern to learn | Yuki implementation target |
|---|---|---|
| LangGraph | Explicit state graphs, checkpointing, replay, human interrupts | Durable run graph with resumable node state and deterministic recovery |
| CrewAI | Role/capability-based specialist teams | Typed specialist manifests, task contracts, delegation budgets |
| OpenAI Agents SDK | Small agent loop, handoffs, guardrails, tracing | Stable agent/tool/handoff contract without provider lock-in |
| Google ADK | Hierarchical/event-driven orchestration | Supervisor, specialist workers, event-driven wake scheduler |
| Claude Code / Codex / Gemini CLI | Repository context, coding loops, subagents, approvals, isolated execution | Sandboxed engineering workspace with patch/test/review lifecycle |
| LlamaIndex | Data ingestion, retrieval and document workflows | Provenance-aware retrieval and hybrid search |
| Pydantic AI | Typed dependencies and validated structured outputs | Schema-constrained provider adapters and typed agent outputs |
| Langfuse / Phoenix / LangSmith | Traces, evaluations, debugging and production observability | Provider-neutral traces, run replay and evaluation dashboards |

Framework comparisons are directional; verify current upstream APIs before adopting dependencies. Yuki should borrow architectural primitives, not add several overlapping orchestration frameworks.

## Evidence-based Yuki gaps

### 1. Runtime composition and feature integration — critical

The repository README says the MCP client is not connected to the runtime command/registry/UI flow, and that web chat intentionally exposes no tools. Several capabilities exist as isolated modules rather than complete user journeys.

**Target:** one application composition root that wires authenticated identity, model router, run store, tool registry, central policy, approvals, memory, traces, and interface adapters. No interface should instantiate an alternate policy path.

### 2. Model routing and model quality — high

Yuki has provider foundations, but needs a unified capability registry rather than selecting a model only by provider name. Different tasks need different model profiles.

**Target registry fields:** provider/model ID, context window, input/output modalities, structured-output support, tool calling, streaming, latency class, local/cloud, availability, price metadata, license/terms metadata, and health state.

**Routing:** classify task intent and constraints, filter models by capability, then rank by quality target, privacy/local-first preference, latency and user-configured cost preference. Keep routing explainable and allow a pinned model override. Do not promise free or unlimited external inference.

### 3. Retrieval and memory — high

The main-branch `core/vector_memory.py` is explicitly lexical, not embedding-based. That is a good lightweight baseline, but not semantic retrieval.

**Target:** hybrid retrieval (lexical + embeddings), metadata filters, source provenance, chunk boundaries, deduplication, freshness, optional reranking, citations, and explicit memory lifecycle controls. Keep private/local indexes available. Evaluate retrieval independently before adding autonomous memory consolidation.

### 4. Agent orchestration — high

Yuki has a bounded execution loop and specialist coordination foundations. It needs a durable run graph and consistent agent contract across synchronous chat, durable tasks, and specialist workers.

**Target:** goal → plan → dependency graph → dispatch → tool/agent handoff → observation → checkpoint → final verification. Each step has idempotency key, owner, deadline, cancellation, policy context, retry classification, and trace span. Agent roles never grant authority.

### 5. Coding-agent workflow — high

Yuki should learn from coding agents' engineering loop rather than just generating code.

**Target lifecycle:** inspect repository → create isolated workspace/worktree → plan patch → implement → run focused tests → run full checks → static/security scan → independent review → summarize diff → request approval → apply/merge. Network and filesystem access must be scoped. Never allow model-generated shell commands to run unrestricted on the host.

### 6. Evaluation and regression testing — critical

Unit tests are substantial, but a production AI system also needs task-level quality measurement.

**Target evaluation suite:**
- golden conversations and task scenarios;
- tool selection and argument correctness;
- policy bypass and prompt-injection adversarial tests;
- retrieval recall/precision and citation correctness;
- structured-output validity;
- multi-step completion and recovery;
- model/provider regression comparisons;
- latency, cost/usage, and cancellation metrics.

Use deterministic fake providers in CI and optional opt-in live-model evaluations. Track versions of model, prompt, tools, policy and dataset. Avoid sending private user data to external evaluation services by default.

### 7. Observability and replay — critical

Add OpenTelemetry-compatible traces and structured run events. Capture model/provider, prompt-template version, token usage (exact vs estimated), tool name, argument digest, policy decision, approval ID, latency, retries, and outcome. Redact secrets and sensitive payloads. Provide run inspection and deterministic replay where safe.

### 8. Multimodal capability — medium/high

The project has voice and image-related modules, but README describes several as optional or not end-to-end connected.

**Target:** a modality adapter contract for text, image, audio input/output, and eventually video; capability-aware routing; explicit user consent for camera/microphone; bounded media retention; transcription/vision provenance; streaming and interruption tests.

### 9. Production operations — critical

Before internet-facing deployment: PostgreSQL integration tests, migrations and backup/restore drills, rate limiting at the edge, secrets management, trusted-host/origin/CSRF controls, worker health and lease observability, queue backpressure, structured logs, metrics, alerting, and documented disaster recovery.

Personal owner usage should not have arbitrary Yuki-imposed quotas, but runtime safety still needs bounded concurrency, cancellation, loop detection, and transparent provider costs.

## Priority sequence

| Priority | Workstream | Exit criterion |
|---|---|---|
| P0 | Central policy + approval on every execution adapter | Integration tests prove deny-by-default across terminal, skill, MCP, browser, web and worker paths |
| P0 | One application composition root | CLI, web and worker share identity, provider, memory, run and policy services |
| P0 | Evaluation + tracing baseline | Every model/tool run has trace IDs and a CI golden-task suite |
| P1 | Capability-aware model registry/router | Task-aware routing across local and cloud providers with deterministic fallback |
| P1 | Durable graph orchestration | Checkpoint/resume/replay, cancellation, idempotency and recovery |
| P1 | Hybrid retrieval and provenance | Retrieval benchmark with citations and regression thresholds |
| P1 | Sandboxed coding workspace | End-to-end patch/test/review flow with no host escape |
| P2 | Multimodal interaction | Tested text/image/audio journeys with explicit permissions |
| P2 | Agent marketplace/100 specialists | Only after stable contracts, manifests, routing, evals and authority inheritance |

## Engineering principles

1. Prefer one authoritative implementation per responsibility.
2. Use typed Python contracts and strict schema validation at model boundaries.
3. Keep provider adapters replaceable and normalize errors, retries, timeouts and usage.
4. Separate model reasoning from execution authority.
5. Never treat prompt-injection scanning as an authorization control.
6. Every autonomous loop has a durable state, cancellation path, deadline and explicit budgets.
7. Usage telemetry measures consumption; it does not impose arbitrary owner quotas.
8. Ship vertical slices with tests and measurable acceptance criteria, not disconnected feature modules.

## Current MCP adapter dependency note

The guarded MCP adapter is being developed on the policy/approval integration branch because the current `main` branch does not yet contain `core/tool_policy.py`. PR #630 exposed this dependency mistake in CI and was superseded by PR #631. Keep the dependency relationship explicit until the security stack is merged.

## Model strategy: frontier, efficient and local models

Do not hard-code one global "best model". Public benchmark leaderboards are snapshots with different test coverage, prompting, inference settings, and scoring methodology. Compare models per task using reproducible internal evaluations.

Use three model tiers:
- **Frontier reasoning/coding:** strongest available cloud model for difficult planning, architecture, code review and high-stakes reasoning. Route only when the task warrants the latency and provider cost.
- **Fast/efficient:** smaller or faster cloud models for classification, summarization, extraction, routine tool selection and short transformations.
- **Local/private:** locally hosted open-weight models for private data, offline operation, routine work and predictable marginal cost when hardware permits.

Capability metadata must be measured or verified per provider/model version. At minimum track tool-call reliability, JSON/schema adherence, coding task pass rate, retrieval-grounded factuality, multimodal input/output, context handling, latency, cancellation behavior, and exact-vs-estimated usage. Never infer a capability solely from a model family name.

The November 2026 public landscape includes multiple frontier vendors and open-weight alternatives; independent aggregators disagree on composite rankings and some models have incomplete benchmark coverage. Treat those leaderboards as candidate discovery, not deployment policy. Run Yuki's own task suite and keep raw benchmark source, date, model ID, prompt version, and evaluation configuration with each result.
