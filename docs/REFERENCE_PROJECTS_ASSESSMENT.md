# External Reference Projects: Architecture and Reuse Assessment

**Review date:** 2026-11-26  
**Target:** Yuki (MIT-licensed repository; Linux-first private pilot)  
**Scope:** Architecture and product patterns from Edge0, smolmaxxing, Cicada, MiroFish, and free-for-dev. This is an engineering assessment, not a security certification or legal opinion.

## Executive decision

Borrow the *ideas and interfaces*, not whole repositories. None of the reviewed projects is a drop-in replacement for Yuki's agent runtime. The most valuable near-term work is:

1. Extend and validate Yuki's existing provider boundary for local OpenAI-compatible inference servers (Edge0 and other engines), without coupling orchestration to a specific model runtime.
2. A reproducible hardware/model capability benchmark and model-fit report (smolmaxxing).
3. A bounded scenario-simulation capability with explicit assumptions, seeded populations, deterministic experiment manifests, and uncertainty reporting (MiroFish-inspired; implement independently).
4. A privacy-first optional meeting/transcription workflow, behind explicit capture consent and local retention controls (Cicada-inspired; later milestone).
5. A curated, periodically revalidated infrastructure/free-tier catalogue with provenance and expiry metadata (free-for-dev-inspired; not copied).

**No third-party source files are copied by this change.** The source projects have materially different languages, runtimes, dependencies, licensing implications, or product boundaries. Copying files before selecting a concrete Yuki integration would add maintenance and supply-chain risk without delivering a working capability.


## Private owner-operated deployment decision

Yuki's product boundary is a single-owner, private assistant for the owner's daily work. It is not intended to offer public signup, public APIs, public agent hosting, or a multi-tenant SaaS service. Architecture and integration choices should optimize for that private operating model:

- Keep the application and its control plane private by default. No public listener, public registration, or anonymous access path is part of the product requirement.
- Bind access to the owner's authenticated devices and explicitly approved integrations. Prefer private networking, least-privilege service identities, local-first processing where practical, and owner-controlled backups.
- Do not build multi-tenant billing, public onboarding, or public-facing administration unless the product boundary changes.
- Preserve strong approval gates for consequential actions even though there is one owner. A private assistant can still make mistakes, leak credentials, or damage the owner's data.
- Keep a recoverable audit trail for privileged actions and external side effects; private does not mean unobservable or untestable.

### Open-source reuse policy for this private deployment

The private-use requirement makes reuse of existing projects more attractive and often avoids distribution-triggered obligations when modified software is kept entirely private. It does **not** make every repository's code free to copy, and it does not eliminate all network-use or dependency obligations. Apply this intake policy:

1. **Permissive licenses (MIT, BSD, Apache-2.0):** preferred for direct file or component reuse. Preserve copyright and license text; include Apache NOTICE material where applicable; inspect transitive dependencies and bundled assets separately.
2. **GPL-family components:** eligible for consideration when a complete component can remain private and operationally isolated. Before linking, combining, modifying, distributing, or making it available to anyone else, assess the exact license and deployment pattern. Do not assume a process boundary alone resolves derivative-work questions.
3. **AGPL components:** may be evaluated for a genuinely owner-only, private deployment. If a modified AGPL-covered service is made available over a network, its source-offer obligations can apply to users interacting with it—even if the user set is small. For Yuki, that would mean the owner, not the general public, but it is still an obligation to plan for. Keep AGPL code isolated unless a deliberate integration and compliance review approves a tighter combination.
4. **Missing, custom, or ambiguous license:** do not copy source code or expressive documentation. Public visibility on GitHub is not a grant of reuse rights. Reimplement general functionality independently or obtain permission.
5. **Models, datasets, prompts, binaries and assets:** review their own terms; a repository's top-level license does not automatically cover every artifact it links or bundles.

For every imported component, record repository URL, exact commit, paths copied, upstream copyright holders, SPDX license identifier, retained notices, modifications, dependency tree, security review, and Yuki integration boundary. Generate/update the SBOM and third-party notices as part of the import. This is an engineering intake policy, not a legal opinion; ambiguous or high-impact copyleft integrations should receive a focused legal review before they are adopted.

### Updated reuse posture for the reviewed projects

| Project | Private-Yuki decision | Practical next step |
|---|---|---|
| Edge0 (Apache-2.0 plus separately licensed components) | Eligible for selective reuse or standalone deployment after per-file/dependency review | Test its local OpenAI-compatible server as an external provider; only port native code if measurements justify it |
| smolmaxxing (MIT; llama.cpp patches and models have separate terms) | Eligible for selective tooling/benchmark reuse after upstream and artifact review | Reuse benchmark methodology first; assess patches individually before importing |
| Cicada (Apache-2.0) | Eligible for selected components, but audio capture and platform dependencies need privacy review | Reuse transcript/provenance patterns; consider audio pipeline components when meeting intelligence is scheduled |
| MiroFish (AGPL-3.0) | Can be considered as a private isolated service; do not silently merge its code into Yuki's MIT codebase | Prototype behind a private service boundary and document network-source obligations before owner access is enabled |
| free-for-dev (no declared license found) | Do not copy its list or prose | Use as discovery only; independently verify providers against official sources |

The previous “do not copy MiroFish” statement is intentionally refined: AGPL is not an automatic prohibition on private owner-only use. The decision is to avoid an unreviewed code merge, while allowing an isolated private evaluation if its source and network-use obligations are understood and satisfied.

## Project-by-project findings

### 1. Edge0 — local inference engine

Repository: https://github.com/Edge0-AI/Edge0  
License declared by repository: Apache-2.0. The inference subtree documents additional third-party components and their separate licenses (GGML MIT, cpp-httplib MIT, nlohmann/json MIT, and llama.cpp-derived tokenizer code MIT).

**What it does:** A native C++ inference engine for sparse/MoE models, CLI, local OpenAI- and Anthropic-compatible server, shared native API, streaming, cancellation, multi-platform backends, and model-specific engine selection. It uses storage-backed expert loading and predictive routing to reduce resident memory.

**Yuki lessons:**
- Keep inference behind a stable provider/transport boundary. A local engine should look like another provider, not become the orchestrator.
- Standardize streaming, cancellation, concurrency limits, health checks, model metadata, and error taxonomy.
- Use capability negotiation: model/runtime/backend/device support should be explicit and observable.
- Treat model files, engine binaries, tokenizer assets, and model licenses as separately versioned artifacts.

**Recommended Yuki implementation:** Yuki already has an Ollama adapter, an OpenAI-compatible cloud adapter with endpoint trust restrictions, provider capability metadata, and a provider catalog. Do not create a duplicate provider abstraction. The concrete gap to investigate is a *separate local OpenAI-compatible endpoint mode* with an explicit loopback/private-network trust policy, redirect rejection, bounded response sizes, health/model checks, cancellation/timeouts, and clear local-vs-cloud privacy classification. Validate Edge0's local server against that contract before adding any Edge0-specific code. Do not embed Edge0's C++ engine into Yuki's Python process in the MVP. For OCI's CPU-first private pilot, local inference may be too resource-intensive; keep remote/provider-neutral routing available.

**Reuse decision:** Apache-2.0 permits reuse with required notices and license retention, but the repo contains separately licensed third-party components. No engine code copied. If a future native integration is justified, conduct a file-level license and dependency review and preserve NOTICE/third-party notices.

### 2. smolmaxxing — constrained-device inference research

Repository: https://github.com/hyperionaidev/smolmaxxing  
Repository license: MIT. Its README states that the project patches llama.cpp; upstream and model/checkpoint assets retain their own licenses. Do not assume every linked model, dataset, or binary is MIT-licensed.

**What it does:** Demonstrates model execution on low-memory ARM devices using custom low-bit kernels, quantized/repacked weights, expert paging from flash, prefetching, vocabulary/output-head reduction, and device-measured benchmarks. The repository includes experiment logs, model tooling, benchmark scripts, and device-specific build paths.

**Yuki lessons:**
- Measure on the target device; do not infer feasibility from parameter count alone.
- Track peak RSS, memory bandwidth, storage throughput, prompt length, decode throughput, thermal/power limits, and quality degradation together.
- Maintain an experiment ledger including failures, exact hardware/software revisions, model hashes, quantization, and reproducible commands.
- Make model selection hardware-aware and allow graceful fallback to a smaller model/provider.

**Recommended Yuki implementation:** Yuki already includes a Hugging Face model evaluation CLI and benchmark starter JSON. Extend this rather than creating a second evaluator: add a hardware/runtime snapshot, model artifact digest, quantization, context length, memory budget, prefill/decode measurements, and separate quality/safety tracks. Keep optimization kernels out of Yuki until there is a supported target device and an independently reviewed upstream patch.

**Reuse decision:** MIT code can generally be reused with copyright/license preservation. However, patches that modify llama.cpp must retain upstream licensing and notices; model weights and corpora are separately licensed. No source copied.

### 3. Cicada — live audio, transcription, translation, and meeting intelligence

Repository: https://github.com/wquguru/cicada  
Repository license: Apache-2.0 (per repository metadata and README badge).

**What it does:** Cross-platform desktop/mobile application for system-audio and microphone capture, live bilingual subtitles, transcription, diarization, translation, structured meeting notes, action items, glossary, transcript correction, local records, export, and optional secure sharing. It distinguishes local/on-device speech recognition from cloud processing by platform and provider.

**Yuki lessons:**
- Separate audio capture, speech recognition, diarization, translation, summarization, and persistence into independently testable stages.
- Preserve timestamped source transcript segments; every generated action item or conclusion should link back to source segments.
- Make capture and network behavior visible. Ask permission only when needed; disclose whether raw audio or only transcript text leaves the device.
- Store raw audio, transcripts, derived notes, and shared projections under distinct retention and access policies.
- Treat voiceprints/voice cloning as sensitive biometric data; avoid them in the MVP unless there is a clear opt-in, deletion, and security design.

**Recommended Yuki implementation:** later add a consent-gated meeting ingestion capability with a platform-specific capture adapter, timestamped transcript schema, source-linked notes/action items, local-first storage, export, and explicit deletion. Linux-first means start with Linux desktop audio support only after the core agent platform is stable; do not port the SwiftUI/macOS application wholesale.

**Reuse decision:** Apache-2.0 permits reuse subject to notice/license obligations, but platform capture code is highly OS-specific and brings substantial permission/security surface. No source copied.

### 4. MiroFish — multi-agent scenario simulation

Repository: https://github.com/666ghj/MiroFish  
Repository license: AGPL-3.0.

**What it does:** Builds a graph/world from seed documents, extracts entities and relationships, generates agent personas and memories, configures a simulated environment, runs parallel agent simulations with temporal memory updates, and generates reports with post-simulation interaction. The workflow includes graph construction, environment setup, simulation, report generation, and deep interaction. Its README describes dependencies including an LLM API and Zep Cloud.

**Yuki lessons:**
- A scenario simulator should be a distinct bounded subsystem, not an unrestricted extension of the production task executor.
- Persist a scenario manifest: source material hashes, extracted entities, persona-generation prompt/version, model revisions, random seeds, simulation parameters, tool permissions, and run lineage.
- Keep simulated agents and their statements clearly labelled as synthetic. Simulation output is not factual prediction or evidence.
- Run simulations in an isolated sandbox with no production credentials, no arbitrary network access, bounded tool access, resource limits, and a kill switch.
- Evaluate calibration and sensitivity: repeat runs with varied seeds, compare scenario assumptions, report distributions/ranges, and expose uncertainty instead of a single confident narrative.
- Treat uploaded seed documents as untrusted data; defend against prompt injection and malicious embedded instructions.

**Recommended Yuki implementation:** create an independent scenario-simulation service with a typed scenario/agent/event schema, bounded simulated tools, deterministic run manifests, run budgets, provenance, and uncertainty-aware reports. Begin with small cohorts and synthetic environments; only scale after evaluation. Keep this separate from workflow task execution and security-sensitive migration/control-plane paths.

**Reuse decision:** AGPL-3.0 is a strong network copyleft license. Copying or incorporating MiroFish code into Yuki may trigger source-disclosure obligations for the combined/modified covered work when offered over a network. Yuki is currently MIT-licensed. Do not copy, vendor, or tightly link MiroFish code without a deliberate license strategy and legal review. Reimplement general ideas independently; do not copy expressive code, prompts, docs, or assets.

**Demo:** https://666ghj.github.io/mirofish-demo/ was reachable, but the page exposed no readable content to the review tooling. This review therefore relies on the repository README and source layout, not an interaction-level demo assessment.

### 5. free-for-dev — infrastructure and free-tier catalogue

Repository: https://github.com/ripienaar/free-for-dev  
GitHub repository metadata reports no declared license; no LICENSE file was found at the default branch root during this review.

**What it does:** A community-maintained, categorized list of SaaS/PaaS/IaaS offerings with free developer tiers. Its README explicitly describes an opinionated editorial process and eligibility criteria, and includes cloud, CI/CD, analytics, security, storage, AI, and developer tooling categories.

**Yuki lessons:**
- Keep a curated catalogue with categories, eligibility criteria, source URLs, last-verified timestamps, region/usage limits, and caveats.
- Free-tier claims decay quickly; never encode a README snapshot as authoritative pricing or capacity.
- Distinguish “always free”, time-limited trial, promotional credits, and free quota with overage billing.
- Include security criteria (TLS, SSO/access controls, data location, retention, export/deletion, and service terms), not just price.
- Require budget alerts and explicit approval before connecting a paid or metered provider.

**Recommended Yuki implementation:** a small provider-capability registry in configuration, with official vendor pricing/terms links and a scheduled/manual verification workflow. Treat this repository as a discovery source only, not a source of truth.

**Reuse decision:** no declared repository license was found. Do not copy its README/list entries or prose into Yuki absent explicit permission or a confirmed license. Independently collect facts from official provider pages and cite the official source with a verification date.

## Cross-project architecture recommendations for Yuki

### A. Provider and inference boundary
- Define a provider-neutral chat/streaming contract: request, stream event, cancellation, timeout, token usage, health, model capabilities, and normalized errors.
- Support remote APIs and local OpenAI-compatible servers through the same boundary.
- Keep model registry metadata separate from provider credentials.
- Never let untrusted model output invoke tools directly; route all tool calls through Yuki's policy/approval layer.
- Add per-provider concurrency, rate, cost, and timeout budgets.

### B. Benchmark and model-fit subsystem
- Version hardware snapshots and model/runtime artifacts.
- Store raw benchmark records; derive summary scores without losing the raw evidence.
- Evaluate assistant quality, coding/engineering, and agent/tool-use reliability as separate tracks.
- Require quality and safety gates before promoting a model revision.
- Keep Kaggle experiments isolated from OCI production credentials and runtime databases.

### C. Simulation subsystem
- Separate simulated-world state from workflow and user state.
- Make seeds, inputs, prompts, model revisions, tool permissions, and random seeds immutable per run.
- Bound number of agents, turns, wall time, tokens, storage, and outbound calls.
- Make reports cite source facts versus assumptions versus generated hypotheses.
- Add repeat-run variance and scenario sensitivity before marketing any “prediction” capability.

### D. Meeting intelligence (later)
- Explicit recording consent and visible recording state.
- Local-first audio/transcript storage with retention controls.
- Timestamp/source-linked transcript and notes.
- Redaction before external model calls; user-controlled export and deletion.
- No voiceprint retention or voice cloning in the first iteration.

### E. Third-party intake and license controls
- Require repository URL, exact commit SHA, file path, SPDX license, copyright owner, dependency inventory, and intended modification for every copied component.
- Preserve LICENSE, NOTICE, copyright headers, and attribution.
- Run dependency, malware, secret, and vulnerability scans before integration.
- Maintain a component-level SBOM and third-party notices.
- Treat missing/ambiguous licenses as “do not copy”.
- Treat AGPL and other reciprocal licenses as an explicit architecture/legal decision, not an incidental dependency.

## Prioritized implementation plan

| Priority | Work item | Why | Suggested acceptance criteria |
|---|---|---|---|
| P0 | Local OpenAI-compatible provider adapter contract | Reuses Edge0-compatible serving without runtime coupling | Streaming, cancellation, health, timeout, capability discovery, secret redaction, contract tests |
| P1 | Hardware/model-fit benchmark manifest | Makes model/provider selection evidence-based | Immutable model/runtime hashes, device snapshot, memory/latency/quality records, reproducible report |
| P1 | Provider/free-tier registry with provenance | Helps manage provider choice and spend | Official source URL, checked-at date, region, quotas, overage warning, no auto-provisioning |
| P2 | Isolated scenario simulation MVP | Adds a distinct multi-agent capability | Sandboxed tools, seeded runs, run budgets, source/assumption separation, uncertainty report |
| P3 | Consent-gated meeting intelligence | Adds audio/transcription workflow after core platform hardening | Permission UX, local-first records, transcript provenance, retention/deletion tests |

## Explicit non-goals for this review

- No model weights or checkpoints copied.
- No third-party dependencies installed.
- No MiroFish code copied because of AGPL-3.0 implications.
- No free-for-dev list text copied because no repository license was identified.
- No platform-specific Cicada or native Edge0 engine code copied.
- No changes to Yuki runtime behavior, production credentials, or deployment configuration.

## Sources reviewed

- https://github.com/Edge0-AI/Edge0
- https://github.com/hyperionaidev/smolmaxxing
- https://github.com/wquguru/cicada
- https://github.com/666ghj/MiroFish
- https://666ghj.github.io/mirofish-demo/
- https://github.com/ripienaar/free-for-dev


## Existing Yuki capabilities verified during this review

A targeted source inspection found important overlap that changes the implementation plan:

- `providers/ollama.py` already implements a local Ollama adapter, probes the local server/model, rejects redirects, and validates tool-call messages.
- `providers/openai_compatible.py` already implements an OpenAI-compatible cloud adapter with HTTPS host allowlisting, credential isolation, redirect rejection, request/response bounds, and tool-call normalization. It is deliberately not a generic private/local endpoint adapter today.
- `providers/capabilities.py` and `providers/catalog.py` already expose provider capability/privacy metadata and safe catalog reporting.
- `yuki_evaluate_model.py` and `providers/model_evaluation.py` already provide a Hugging Face model evaluation path; `benchmarks/model_evaluation_starter.json` is present.
- Yuki's existing tests include provider adapter, provider capability, local inference worker, remote inference client, benchmark registry, and evaluation suites.

Therefore the correct next increment is **gap closure and integration testing**, not a parallel provider abstraction or a duplicate evaluator. In particular, assess whether a local OpenAI-compatible endpoint can be supported without weakening the cloud endpoint's strict SSRF/credential boundary. Keep separate constructors/policies for local loopback/private endpoints and public cloud providers; never broaden the cloud allowlist to private IPs as a shortcut.



## Additional GitHub candidate shortlist

A follow-up GitHub discovery pass found four MIT-licensed projects worth evaluating. Their top-level LICENSE files and READMEs were inspected on 2026-11-26. This is a shortlist, not an approval to add dependencies: pin exact commits/releases, inspect package metadata and transitive dependencies, and review security-sensitive execution paths before adoption.

### Browser automation: browser-use

Repository: https://github.com/browser-use/browser-use (MIT; README describes a Python browser agent using a persistent REPL and Chrome DevTools Protocol).

**Fit:** high potential capability gain. Yuki can delegate browser tasks to a separate browser worker rather than implementing browser control, DOM interaction, navigation and browser lifecycle from scratch.

**Risks and integration boundary:** a persistent model-authored Python REPL plus CDP is a powerful execution surface. Run it in a disposable, least-privilege worker with isolated browser profile, explicit domain/network policy, download/upload controls, secret isolation, time and resource budgets, and owner approval for consequential submissions or purchases. Never give the browser worker Yuki's database credentials or general host filesystem access. Validate browser-use's current architecture and pinned release before integration; the project evolves quickly.

**Decision:** first candidate for a focused integration spike. Prefer a separate worker/service and typed task API; do not embed its REPL inside the main Yuki process.

### Stateful orchestration: LangGraph

Repository: https://github.com/langchain-ai/langgraph (MIT; README describes durable execution, human-in-the-loop interrupts, and long-running stateful agents).

**Fit:** useful reference for checkpointing, resumable execution and interruption semantics.

**Risks and integration boundary:** Yuki already has durable task/workflow/goal primitives, dependency propagation, outbox delivery and approval controls. Replacing those wholesale would duplicate concepts and risk breaking established invariants. Inspect only specific gaps (checkpoint replay, pause/resume, state inspection) and compare them with Yuki's existing semantics.

**Decision:** architecture reference and selective component candidate; no framework adoption until a gap analysis proves a net reduction in complexity.

### Agent SDK: OpenAI Agents SDK for Python

Repository: https://github.com/openai/openai-agents-python (MIT; README describes agents, tools, handoffs, guardrails, human-in-the-loop, sessions and tracing, with support for multiple model providers).

**Fit:** potentially useful for tool handoffs, agent delegation patterns and tracing conventions.

**Risks and integration boundary:** Yuki already has provider adapters, agent execution, approval/policy gates, MCP integration and workflow orchestration. Adding a second runtime may split authority and weaken consistent permission enforcement. All tool execution must remain behind Yuki's central authorization and approval layer, regardless of which SDK produced a tool request.

**Decision:** selectively reuse patterns or isolated adapters only; do not replace Yuki's central execution/policy runtime.

### Minimal graph framework: PocketFlow

Repository: https://github.com/The-Pocket/PocketFlow (MIT; README advertises a small, dependency-free graph/flow abstraction with multi-agent and workflow patterns).

**Fit:** small and easy to inspect; may be useful for short-lived deterministic pipelines where a full durable workflow is unnecessary.

**Risks and integration boundary:** its minimalist model is not a substitute for Yuki's persistent tasks, fencing, outbox, retries, audit events or cancellation semantics. Duplicating the existing workflow layer would create two execution models.

**Decision:** keep as a reference for lightweight in-process composition; no current need to import it.

### Candidate ranking

| Rank | Candidate | Reuse approach | Reason |
|---|---|---|---|
| 1 | browser-use | Isolated worker integration spike | Adds a concrete capability Yuki can expose through its existing tool/approval boundary |
| 2 | LangGraph | Selective pattern review | Strong durable-agent concepts, but substantial overlap with Yuki's workflow engine |
| 3 | OpenAI Agents SDK | Pattern/adapter review | Useful handoff and guardrail ideas, but runtime duplication risk |
| 4 | PocketFlow | Reference only | Tiny abstraction, but Yuki already has durable orchestration |

For all four, MIT applies to the repository code under its LICENSE; separately packaged assets, examples, model artifacts, optional integrations and transitive dependencies must still be checked independently. No source files or dependencies from these candidates are imported by this assessment.


## Existing Yuki browser capability: implementation review

A code-level review of the Yuki branch found that browser reading already exists; it is not a greenfield capability. Relevant files are:

- `automation/browser.py`: `SafeBrowser` uses Playwright Chromium, explicit HTTPS host allow-listing, public-IP DNS checks, per-request routing checks, and optional central authorization bound to a SHA-256 digest of the full URL.
- `skills/browser_skill.py`: exposes a human-approved, read-only `browser_read` skill and scans returned page text as untrusted content.
- `tests/test_browser_authorization.py`: verifies principal/resource binding, policy denial, audit failure, and gateway/principal pairing.
- `requirements-browser.txt`: keeps Playwright optional.

This changes the integration recommendation: **do not add browser-use as a second browser runtime yet.** First harden and complete Yuki's existing reader. Browser-use can later be evaluated as an alternative isolated worker only if it provides a clearly measured capability gap (for example robust interactive workflows), not simply because it is popular.

### Security findings to close before interactive automation

1. **Network isolation is still required.** `SafeBrowser._validate()` resolves a host and rejects non-global IPs, but Chromium performs its own subsequent DNS resolution. The check and actual connection are not atomically pinned to the same resolved address. A DNS-rebinding/TOCTOU window therefore remains. A dedicated browser worker must enforce network egress outside the browser process (private/link-local/loopback/metadata destinations blocked at the network namespace or firewall) or use a transport that pins and verifies the connection destination. Application DNS validation alone is not a complete SSRF boundary.
2. **Browser cleanup must be exception-safe.** `read_page()` closes context/browser on the success path, but failures during navigation, title/text extraction, routing or response handling can bypass those close calls. Use nested context managers or `try/finally` so page context and browser always close, including cancellation and timeout paths.
3. **The skill currently constructs `SafeBrowser` without a central gateway/principal.** The skill advertises human approval, but that is not the same as binding every outbound browser request to the shared authorization gateway. Production wiring must inject the authenticated owner principal, current policy revision, task/delegation/approval context, and the exact shared gateway; fail closed when those are absent in production mode.
4. **Redirects and subresources need explicit regression coverage.** The request route attempts to authorize each URL, but tests should cover redirect chains, cross-host scripts/iframes, blocked private IPs, malformed URLs, and denial after a policy revision change. Verify the browser cannot proceed when audit persistence fails.
5. **Bound the worker, not just the returned text.** Enforce wall-clock deadline, navigation/redirect count, page count, CPU/memory/process limits, response/download size, and cancellation. `inner_text()[:12000]` bounds returned text only; it does not bound the amount of content Chromium must load or parse first.
6. **Keep page content untrusted.** Preserve the current untrusted-content marker and prompt-injection scan, but do not treat a scan result as proof of safety. Page content must never modify tool permissions, system instructions, host allow-lists, or owner identity.

### Safe implementation sequence

- Phase 1: harden `SafeBrowser` lifecycle and tests; require production authorization injection; document and configure OS/container-level egress isolation.
- Phase 2: expose a typed read-only browser task through Yuki's existing task/approval pipeline, with bounded outputs and provenance.
- Phase 3: only if required, add interactive browser actions in a separate disposable worker with per-action authorization and owner confirmation for submissions, purchases, account changes, uploads, or other consequential actions.
- Phase 4: benchmark browser-use against the hardened native reader on real owner workflows; adopt it only if reliability/capability gains justify another execution runtime.

The current browser reader is useful groundwork, but it is not yet evidence that unrestricted or interactive browser automation is safe for production use.
