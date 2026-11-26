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


## First-party interfaces: CLI and desktop

A follow-up review of the current repository confirms that Yuki already has two substantial first-party interfaces. They are part of the product architecture, not optional future placeholders. The target is one private owner-operated Yuki runtime with multiple clients, not separate CLI and desktop products with divergent business logic.

### CLI

- `yuki_cli.py` exposes the installed `yuki` entry point declared in `pyproject.toml`.
- Current commands include `doctor`, `setup`, `provider-status`, `provider-test`, `ask`, persistent `chat`, and `auth bootstrap-admin`.
- Persistent chat uses the local conversation runtime, owner-scoped conversation records, version checks, and explicit `tools=[]`; it does not silently expose local tool execution.
- Provider diagnostics distinguish configuration/presence from a verified live connection and sanitize provider exceptions.
- `tests/test_yuki_cli.py` and `tests/test_cli_packaging.py` cover command behavior, persistent chat cleanup, provider error handling, portable installed paths, and the stable console entry point.

**CLI priorities:** preserve stable command/exit-code behavior; add machine-readable output and documented automation contracts where needed; expose task inspection, approval review, cancellation, diagnostics and backup/restore through the same authenticated application services; ensure non-interactive commands cannot bypass owner authentication or approval. Never add an unrestricted shell passthrough as a convenience feature. Keep secrets out of argv, environment dumps, logs and shell history.

### Desktop

- `ui/desktop/launch.py` owns the PySide6 process lifecycle and defers Qt imports so the core/CLI do not require the desktop dependency.
- `ui/desktop/shared_desktop_composition.py` composes authenticated conversation services with the existing specialist controller bridge.
- Desktop authentication uses the local owner account and OS keyring-backed session handling; saved memory is encrypted and its key is stored through the OS credential store.
- Dedicated modules cover storage, lifecycle, backup, migration, security service/dashboard, conversation and voice integration.
- `ui/desktop/PACKAGING.md` and `.github/workflows/desktop-packages.yml` describe native Windows/macOS/Linux unsigned bundles, smoke tests, Python-environment SBOMs, checksums and build provenance.

**Desktop priorities:** complete clean-machine install/upgrade/uninstall/rollback acceptance on each target OS; verify OS keyring behavior across supported Linux desktop environments; finish signing/notarization and full bundled-binary license/notice inventory before production installers; test backup/restore and profile migration; add UI acceptance tests for expired sessions, approval changes, cancellation, provider failures, accessibility, and shutdown with in-flight work. CI smoke tests are necessary but do not replace physical clean-machine validation.

### Shared interface contract

1. Both interfaces call the same application/service layer for identity, authorization, memory, conversations, tasks, workflows, provider selection and audit.
2. UI visibility is never authorization. A hidden/disabled button or a CLI command omission is not a security control; every operation is checked again by the shared service.
3. Desktop background workers and CLI subprocesses receive narrowly scoped task context, not ambient database, cloud, keyring or host credentials.
4. Owner approval is bound to the exact action, resource, parameters, policy revision and expiry; stale approvals must be rejected at execution time.
5. Task status, cancellation, retries, audit events and result provenance must have the same semantics regardless of which interface submitted the request.
6. CLI remains useful for headless Linux administration and automation; desktop remains the rich interactive owner console. Neither is a public API or public multi-user service.
7. Keep platform-specific packaging and OS integration at the interface boundary. Core behavior remains portable; Linux is the first production deployment target, with Windows support retained as a client target and hardened after Linux readiness.

### Readiness interpretation

The presence of CLI/desktop modules, tests and CI packaging workflows is evidence of implemented foundations, not proof of production readiness. The desktop workflow currently creates unsigned developer/CI bundles; the Python SBOM does not inventory every OS-level or PyInstaller-bundled binary. The product must not be described as having signed installers, clean-machine acceptance, production-grade cross-platform deployment, or a fully unified authorization path until those gates are demonstrated.


### CLI authentication boundary: explicit finding

A targeted source review of `yuki_cli.py`, `core/local_cli_runtime.py`, `core/local_owner_identity.py`, and `core/auth.py` found an important distinction that the shared-interface contract must preserve:

- `yuki chat` creates/loads a stable local owner identifier and uses the shared conversation application runtime. It does not authenticate that invocation through `BuiltInAuthService.authenticate()` or resolve a session.
- The local CLI runtime intentionally installs an empty tool registry with a deny-all authorizer (`tools=[]`). This is a meaningful privilege boundary: chat cannot silently invoke registered local tools.
- `yuki ask`, `provider-test`, and provider diagnostics initialize the configured provider directly and are not gated by the local Yuki account session.
- `yuki auth bootstrap-admin` creates the first local admin account, but does not issue a CLI session. The desktop login path uses the OS credential store for desktop sessions.

This is not automatically a vulnerability on a single-user workstation where the operating-system account and profile directory are the trust boundary. It does mean the local Yuki account currently protects the desktop workflow more directly than it protects CLI invocations. Anyone who can execute Yuki as the same OS user and read its configuration/profile may be able to use provider access and the local conversation store without entering the Yuki account password.

**Required decision before expanding CLI privileges:** document the supported threat model as either (A) the OS account is the sole authentication boundary for headless CLI operations, backed by strict profile/config/key permissions and OS-level account isolation, or (B) CLI commands require an explicit Yuki owner login/session. Do not claim that the CLI is protected by the Yuki owner account until option B is implemented and tested.

For either choice:
1. Keep tools deny-all by default. Any future CLI tool execution must use the shared authorization gateway, exact-action approvals, current policy revision checks, and durable audit.
2. Do not put bearer tokens/passwords in command-line arguments, shell history, process listings, or environment dumps. If CLI sessions are introduced, use an OS credential store where available or a short-lived protected local credential channel.
3. Restrict config, provider credentials, conversation DB, and local owner identity files to the owning OS account; validate permissions on startup and fail closed or provide a safe remediation command.
4. Add tests proving unauthenticated invocations follow the chosen threat model, that another OS profile cannot access the owner data, and that expired/revoked CLI sessions cannot perform operations if session auth is selected.
5. Clearly separate read-only diagnostics from billable provider calls: `provider-test` and `ask` may send content to an external provider, and the CLI must make this explicit in help/output and require configured cloud opt-in consistent with the desktop/runtime policy.

Until this decision and its tests are complete, CLI should be described as a local owner-shell interface with deny-all tool execution—not as independently authenticated against the Yuki owner account.


### Local profile directory permissions: hardening gap

A focused review of `core/local_owner_identity.py`, `core/sqlite_file_security.py`, and `core/portable_paths.py` confirms useful file-level protections already exist on POSIX:

- Local owner identity files are created with mode `0600`, opened without following a final-component symlink where supported, required to be regular files, and rejected if group/other permission bits are present.
- SQLite database files are opened without following a final-component symlink on POSIX, required to be regular single-link files, and tightened to `0600`.
- Existing tests cover database file modes, symlinks, hard links, and SQLite WAL/SHM sidecars.

However, the parent-directory boundary is not consistently enforced:

- `load_or_create_local_owner_id()` calls `Path.mkdir(parents=True, exist_ok=True)` without explicitly applying or verifying owner-only permissions on the profile directory.
- `prepare_private_sqlite_path()` resolves the parent and creates missing parents with `mode=0700`, but it does not reject an already-existing parent directory whose permissions allow other local users to traverse/read it.
- `ensure_private_directory()` in `core/portable_paths.py` creates missing components with `0700`, but deliberately leaves existing directories unchanged. It also resolves paths before validation, so callers must not treat it as a complete symlink/path-confinement defense.
- Windows relies on user-profile/installer ACLs; the POSIX mode checks do not prove Windows ACL isolation.

**Risk:** owner-only database files inside a broadly accessible directory are not a complete private-profile boundary. Another local user may be able to enumerate names, access sidecars or exploit permissive directory traversal depending on directory ACLs and platform behavior. This is especially relevant to a shared Linux host/VPS and multi-user workstations. It is a defense-in-depth concern on a genuinely single-user machine, not proof of remote account compromise.

**Remediation before shared-host deployment:**
1. Define one canonical private profile root for CLI and desktop, and create/validate it before any identity, config, database, keyring metadata, log, backup, or sidecar file is opened.
2. On POSIX, require the profile directory and all Yuki-owned sensitive subdirectories to be owned by the effective user and have no group/other permissions (normally `0700`). Fail closed on unexpected ownership, symlink components, or unsafe modes; do not silently chmod arbitrary caller-supplied directories without a clear repair flow.
3. For custom `YUKI_HOME` and workspace overrides, validate the resolved location and its ownership/permissions; distinguish an explicitly shared workspace from the private identity/credential root.
4. On Windows, verify and document inherited and explicit ACLs for the profile, config, database, WAL/SHM, backups and logs. Add Windows CI tests where possible and clean-machine installer acceptance.
5. Add tests for permissive existing parent directories, symlinked parent components, owner mismatch where testable, and custom profile roots. Ensure error messages are actionable but never disclose secrets or sensitive paths unnecessarily.
6. Treat same-OS-user processes and administrator/root as inside the local trust boundary unless a stronger isolation mechanism is explicitly introduced.

Do not claim that Yuki's complete profile is private merely because individual SQLite files are `0600`. The current tests establish file-level POSIX protections, not complete directory/ACL isolation.


### CLI identity/workspace coupling: confirmed implementation detail

Further call-chain inspection confirms the CLI currently invokes:

```python
load_or_create_local_owner_id(config.get("workspace"))
```

The default configuration sets `workspace: ./yuki_workspace`. The conversation store factory independently constructs `workspace/conversations.sqlite3`. Therefore, both the local owner identifier and conversation database are rooted in the workspace, rather than a separately defined private application-data directory.

A follow-up call-chain check clarifies the exact protection boundary:

- `ConversationStore.__init__()` does call `prepare_private_sqlite_path()`, which protects the final SQLite file against final-component symlinks and hard links and applies POSIX mode `0600`.
- That helper creates missing parent directories with `0700`, but does not reject an already-existing permissive parent. The SQLite file-level protection therefore does not establish a private directory boundary.
- `load_or_create_local_owner_id()` still creates its parent with `mkdir(parents=True, exist_ok=True)` and checks the identity file mode, but does not validate parent ownership or directory permissions.
- `portable_paths.data_root()` still returns the repository root for a source checkout containing `config.yaml`; `desktop_data_root()` then places desktop data under the checkout's `.yuki` child. Installed defaults differ. This behavior must be handled explicitly rather than silently changing paths for developers or portable installs.


This is a stronger and more specific finding than a generic directory-mode concern:

- A workspace can be an ordinary project directory, a shared mount, a synced folder, or a user-selected external path.
- Treating it as the trust root couples personal identity and conversation persistence to project/workspace sharing choices.
- A custom workspace may have broad directory permissions even though the identity and SQLite files themselves are owner-only.
- The identity file is a stable local conversation owner key, not an authentication credential. Its presence must never be described as CLI login.

**Recommended correction (separate code PR):**
1. Resolve a canonical private application-data root independent of `workspace` (XDG data directory on Linux; platform-appropriate application data directory elsewhere; explicit `YUKI_HOME` semantics documented).
2. Store the local CLI owner identity under that private root, never under the workspace.
3. Keep the conversation database under the private data root by default. If the user explicitly configures a separate database path, validate its directory boundary and warn or fail when it is not private.
4. Treat workspace only as an execution/project working directory; it must not implicitly choose identity, credentials, or personal conversation storage.
5. Provide an explicit, tested migration path from the existing `workspace/local-owner-id` and `workspace/conversations.sqlite3` locations. Preserve the existing owner ID and database contents; do not silently generate a new owner or orphan history.
6. Preserve an explicit compatibility/opt-in mode for users who intentionally want portable workspace-local data, but require a clearly named setting and private-directory validation.
7. Add regression tests proving that changing workspace does not change owner identity or conversation storage, that project workspaces can be shared without exposing personal data, and that migration preserves owner ID/history.

Do not move existing user data automatically until migration behavior, backup/rollback, and collision handling are implemented and tested. This audit documents the required boundary; it does not claim the code has been changed.

### Private-profile validator prototype (not yet wired into application startup)

A small first implementation increment now exists in `core/private_profile.py`, with focused tests in `tests/test_private_profile.py`.

- On POSIX, it creates a missing final profile directory as owner-only (`0700`) and validates the final directory through a no-follow directory file descriptor.
- It rejects an existing final directory with group/other permissions, rejects a final-component symlink, verifies directory type and effective-user ownership, and deliberately does not silently chmod an existing unsafe directory.
- It fails closed on Windows because this prototype does not yet verify Windows DACLs. A Windows ACL adapter and installer/clean-machine acceptance tests are still required.
- It validates the final profile directory only; parent-component symlink/path confinement and ownership of the broader ancestor chain are not claimed by this prototype.
- It is not yet called by CLI, desktop, identity, or database startup. Existing storage locations and user data are unchanged.

This is a deliberately isolated foundation, not a completed private-profile migration. Do not describe the application profile as secured end-to-end until the canonical path policy, Windows ACL implementation, parent-chain policy, migration planner, consistent SQLite backup/restore, collision handling, and rollback are complete and integrated.

### Private-profile implementation sequencing gate

The source audit is not yet a migration implementation. The next code change should introduce a dedicated private-profile abstraction without changing existing storage paths in the same commit. It must have independently testable contracts for:

1. Source checkout, installed package, Linux XDG, Windows application-data, macOS application-support, and explicit `YUKI_HOME` behavior.
2. A canonical private root distinct from project workspace; identity, conversation DB, credentials metadata, logs, backups and SQLite sidecars must resolve beneath the intended private root unless a separately reviewed override is used.
3. POSIX directory ownership/mode and symlink-component validation; Windows ACL validation must be implemented or the deployment must explicitly remain limited to the protected current-user profile with documented acceptance tests.
4. A migration planner that inventories legacy identity/database locations, validates source and destination, takes a consistent SQLite backup (including WAL state), preserves the owner ID and conversation rows, refuses collisions without overwriting, and records a rollback path.
5. A two-phase rollout: first ship the path abstraction and migration dry-run/report; only after CI and backup/restore tests pass should CLI composition and SQLite store defaults switch.

Do not use `Path.resolve()` alone as a symlink defense: it follows links before the caller can inspect the original path components. Do not silently chmod arbitrary existing user directories, and do not move or delete legacy files until a verified backup and rollback plan exists.


### Follow-up: data-root semantics and CI failure classification (2026-11-26)

A second pass over the actual path helpers narrows the migration design:

- `core/portable_paths.py:data_root()` deliberately returns the repository root whenever a source-checkout `config.yaml` exists; installed wheels use the OS data directory unless `YUKI_HOME` overrides it.
- `desktop_data_root()` is currently `data_root() / ".yuki"`. Consequently, a source checkout stores desktop state beneath the checkout's `.yuki` directory, while an installed build uses the per-user data root's `.yuki` child. A future canonical profile service must preserve/document this distinction or deliberately migrate it; it must not assume `desktop_data_root()` is always an OS-standard private directory.
- `core/local_cli_runtime.py` passes the configured workspace explicitly to `load_or_create_local_owner_id()`, overriding that function's default `data_root()` behavior.
- `core/conversation_store_factory.py` independently places SQLite conversations at `workspace/conversations.sqlite3`. The shared `ConversationApplicationRuntime` delegates persistence to that factory, so changing only the owner-ID call would leave personal history coupled to the workspace.
- Existing `tests/test_portable_paths.py` verifies basic path selection and `tests/test_conversation_store_factory.py` verifies SQLite default construction, but the inspected test paths do not establish a complete identity/database migration contract. The expected owner-identity test filename was not present at the inspected branch path; no coverage claim is made from that missing file.

**Implementation sequencing:** first introduce a single, tested private-profile path/permission abstraction with explicit source-checkout, installed, and `YUKI_HOME` behavior. Then add a migration planner that detects legacy identity/database locations, validates both source and destination, takes a consistent SQLite backup, preserves the owner ID, handles destination collisions without overwrite, and supports rollback. Only after those tests exist should CLI composition and the SQLite factory switch defaults. Keep explicit workspace-local portable mode opt-in. This is intentionally not a one-line path substitution.

**Latest PR #1346 CI observation:** the Ubuntu Python 3.11 job (job `139709210857`) ran a substantial portion of the test suite successfully, then the GitHub Actions runner emitted a shutdown signal and the operation was cancelled during OAuth credential-vault tests. The available log excerpt contains no Python assertion failure or traceback preceding runner shutdown. Classify this run as **infrastructure/runner interruption, inconclusive**, not as a confirmed product-code regression and not as a passing test run. Re-run the job on the same head before merge consideration.


### Existing browser reader: security review before any browser-use integration

A direct review of the current `automation/browser.py` and `skills/browser_skill.py` changes the integration recommendation: Yuki already has a small read-only Playwright browser skill. Do not add a second browser framework until this implementation's network and resource boundaries are hardened.

**Existing positive controls:**
- navigation starts with an HTTPS-only URL validator and exact hostname allow-list;
- the initial hostname is resolved and rejected if any returned address is non-global;
- request interception blocks non-HTTPS and non-allow-listed hostnames;
- downloads are disabled and service workers are blocked;
- the result is marked untrusted and scanned for prompt-injection indicators;
- the skill declares medium risk and requires approval.

**Security gaps requiring a focused code change and tests:**

1. **DNS rebinding / resolution-to-connection gap:** the initial `socket.getaddrinfo()` check is not bound to Chromium's eventual connection. The Playwright route handler checks scheme and hostname only, not the resolved destination address. A hostname that resolves publicly during validation but privately when Chromium connects could bypass the intended public-destination check. Route interception by hostname alone is not an SSRF boundary.
2. **Subresource port policy mismatch:** the initial navigation validator limits the URL to port 443, but the route handler permits any HTTPS port for subresources. Enforce the same explicit port policy for every request.
3. **Unbounded page extraction:** `inner_text()` reads the body before truncating the returned string to 12,000 characters. The output limit therefore does not bound browser memory/CPU consumed by a very large or adversarial DOM. Enforce response/document/resource budgets before extraction, with an explicit timeout and page-content limit.
4. **Browser lifecycle on exceptions:** browser/context cleanup is on the success path. Exceptions during navigation, title or text extraction can skip explicit close calls. Use nested context managers or `try/finally` so page, context and browser are always closed.
5. **Constructor/config validation:** validate the allowed-host entries and timeout bounds at construction; reject malformed host values rather than silently normalizing arbitrary input.

**Required test matrix before expanding capabilities:** redirect to a disallowed host; redirect and subresource to non-443 ports; mixed public/private DNS answers; DNS rebinding or mocked connection-time resolution; IPv4 and IPv6 loopback/link-local/private destinations; oversized response/DOM; navigation and extraction timeout; browser launch/navigation/extraction exceptions with cleanup assertions; download attempts; service-worker attempts; and prompt-injection content remaining explicitly untrusted.

**Architecture decision:** keep the current skill read-only and approval-gated. Any future browser-use persistent REPL/CDP worker must be a separate disposable least-privilege service with no Yuki database, cloud credentials, general host filesystem, or ambient shell access. It must reuse the central approval and audit services, enforce connection-time network policy outside page JavaScript, and have strict CPU/memory/time/download budgets. Do not simply swap Playwright for browser-use or embed a persistent REPL in the main Yuki process.


### Browser skill approval-path trace (2026-11-26)

A follow-up source trace confirms that Yuki has a central guarded skill invocation mechanism, not merely descriptive `requires_approval` metadata:

- `skills/registry.py` constructs and registers `BrowserSkill`.
- `BrowserSkill` declares `risk = "medium"` and `requires_approval = True`.
- `core/approved_skill_runtime.py:ApprovedSkillRuntime.invoke()` delegates to `invoke_registered_skill()` in `core/guarded_skill_runtime.py`.
- `invoke_registered_skill()` treats `requires_approval=True` as requiring explicit invocation approval, consumes a one-shot approval bound to the exact skill name and SHA-256 digest of the exact argument, and records a bounded status through its audit callback.
- Persistent manifest approval in `SkillApprovalStore` is a separate registration/wiring control; it is not a substitute for per-invocation approval.

**Important boundary:** this confirms the central guarded path and its semantics. It does not, by itself, prove every current caller or future agent/desktop/CLI entrypoint routes browser invocations through `ApprovedSkillRuntime`. The runtime docstring explicitly requires callers to route execution through that boundary, and the guarded runtime is not a sandbox. Before browser code is enabled in any new execution surface, trace that caller end-to-end and test that a missing, mismatched, expired-by-process-restart, or already-consumed approval blocks execution. The one-shot invocation approval is process-local and is intentionally not durable across restart.

**Readiness decision unchanged:** the central approval mechanism is a useful control, but it does not mitigate DNS rebinding, unbounded page extraction, cleanup leaks, or the need for connection-time egress enforcement. Keep the browser skill unavailable to unattended/high-autonomy execution until those network/resource controls and caller-path tests are in place.


### Browser reader hardening increment (2026-11-26)

A focused code/test increment was pushed to the existing assessment PR (no third-party browser framework or dependency added):

- `SafeBrowser` now validates exact ASCII/IDNA hostnames and bounded timeout, page-text, and request-count settings.
- Initial URL parsing handles malformed ports and continues to reject credentials, non-HTTPS URLs, non-443 ports, and non-allow-listed hosts.
- Every intercepted request now applies the same HTTPS/443/exact-host policy, closing the previous subresource-port mismatch.
- A per-navigation request-count ceiling rejects excess requests.
- Page text extraction walks text nodes with both a character budget and a maximum visited-node budget; Playwright evaluation also receives a bounded timeout instead of first materializing all body text with `inner_text()`.
- Browser and context cleanup now runs in `finally`, including extraction/navigation failures.
- Added `tests/test_browser_security.py` for configuration rejection, URL/port checks, mixed public/private DNS answers, subresource policy, bounded character/node traversal, extraction timeout propagation, and cleanup after failure.

**Residual security limitations (not resolved by this increment):** the pre-navigation DNS lookup is still not cryptographically or operationally bound to Chromium's connection. Playwright interception validates request URLs/hostnames but does not prove the connected peer IP. The request-count, text-node, character, and evaluation-time limits bound parts of extraction, but do not bound response-body bytes, aggregate transfer, browser HTML parsing/memory cost before DOM extraction, or total browser-process resource consumption. A malicious page can therefore still consume substantial browser resources, and DNS rebinding/SSRF is not closed.

**Deployment gate:** do not expose this browser process to untrusted URLs or unattended/high-autonomy tasks. Before enabling it beyond a tightly controlled owner-approved experiment, run Chromium in a disposable isolated worker with connection-time egress enforcement (policy proxy or network namespace/firewall), bounded response/body and total transfer budgets, CPU/memory/process limits, and integration tests proving redirects, subresources, IPv4/IPv6, mixed DNS and rebinding cannot reach loopback, private, link-local, or metadata destinations. The new unit tests are authored but have not been run locally; rely on the PR's GitHub Actions results for execution evidence.


### Browser worker reuse assessment: existing sandbox contract (2026-11-26)

A source inspection of `core/sandbox_policy.py` and `core/sandbox_runtime.py` establishes an important integration boundary:

- `SandboxPolicy` has a default `network_mode="none"` and only two declared modes: `none` and `allowlist`.
- `build_docker_argv()` emits Docker `--network none` for the no-network mode.
- It explicitly rejects `network_mode="allowlist"` with `SandboxRuntimeError("allowlist mode requires a separately enforced egress gateway")`.
- `validate_runtime_capabilities()` rejects runtime declarations because trusted host-side enforcement/attestation is not implemented. Docker daemon self-reported diagnostics are explicitly not proof of network isolation, resource enforcement, or cleanup.

**Decision:** reuse the existing sandbox policy/resource model as a starting point, but do not claim it can currently host an allow-listed browser worker. The present Docker argv builder supports no-network isolation only; it does not implement an egress proxy, per-destination connection policy, or trusted verification that such a gateway is attached. Do not weaken its fail-closed rejection or set capability booleans to make browser execution appear supported.

**Required extension before browser worker implementation:**

1. A dedicated egress gateway/proxy with an authenticated worker-to-gateway channel; the worker must have no direct route to external networks, host services, metadata endpoints, or the Yuki control plane.
2. Resolve and validate destination addresses at connection time, pin the selected address for that connection, reject any non-global IPv4/IPv6 destination, and repeat policy evaluation for every redirect and subresource. Validate TLS SNI/hostname against the requested allow-list host.
3. Enforce HTTPS/443 and exact-host allow-listing at the gateway, not only in Playwright request interception.
4. Enforce maximum response bytes, aggregate transfer bytes, request count, connection count, wall-clock time, CPU, memory, process count, and temporary storage at the gateway and worker boundary.
5. Bind each worker launch to a short-lived owner-approved task capability and record policy revision, image digest, gateway policy digest, task ID, and bounded audit outcome. No host credentials, provider tokens, database sockets, or general workspace mounts.
6. Add integration tests with a controlled DNS/HTTP/TLS test harness covering rebinding between lookup and connect, mixed A/AAAA answers, redirects, subresources, IPv4/IPv6 loopback/private/link-local, cloud metadata IPs, disallowed ports, oversized responses, connection exhaustion, gateway loss, and worker cleanup.
7. Introduce trusted host-side verification before any runtime capability declaration can be treated as evidence. A Docker info response or self-reported boolean is not an attestation.

This is an architecture gate, not a claim that an egress gateway or browser worker exists. The current browser reader remains unsuitable for untrusted URLs and unattended agent execution.
