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
