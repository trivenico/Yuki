# Yuki Native Brain — architecture decision and delivery roadmap

**Status:** accepted product direction; implementation is incremental  
**Decision:** Yuki must operate as an independent, local-first AI assistant. External model APIs are optional adapters, never required for core startup or conversation.

## Product definition

Yuki is a personal AI companion and agentic assistant with its own identity, model runtime, memory, knowledge, reasoning/planning loop, tools, voice and multimodal interfaces. It should feel like a capable always-available assistant while remaining transparent, privacy-preserving and under user control.

"Own brain" means Yuki owns the complete intelligence system and runtime lifecycle—not that we must immediately pretrain a frontier-scale foundation model from random initialization. A responsibly licensed open-weight model is the initial neural foundation. Yuki's own orchestration, memory, retrieval, tools, evaluation, personalization and future fine-tuning form the product intelligence around it. We can later train smaller native models and, if compute/data justify it, pretrain a foundation model.

## Non-negotiable principles

1. **Local-first, no API key required.** Fresh install must offer a useful local-model path. Cloud providers are explicitly enabled optional adapters.
2. **Yuki owns the control plane.** Conversation state, model selection, prompt assembly, memory, retrieval, tool schemas, policy, approvals, execution, audit and evaluation belong to Yuki—not to a provider's hosted agent runtime.
3. **Model is replaceable.** The model is a capability behind a stable interface. Never bind memory, tools or identity to one vendor/model family.
4. **No silent cloud fallback.** If the local model is unavailable, explain the issue and offer an explicit user choice. Never transmit prompts to a cloud provider without consent and visible routing.
5. **Memory is not training.** Personal memories remain user-owned runtime data. They are not automatically added to model-training datasets.
6. **Learning is evaluated and reversible.** Fine-tunes/adapters are versioned artifacts; promotion requires benchmark, safety, regression, license and human approval gates. Rollback is always available.
7. **Data provenance first.** Every training dataset needs source, license/terms, collection date, permitted use, transformations, deduplication, PII policy and lineage. Publicly downloadable does not mean unrestricted training permission.
8. **Secure autonomy.** Model-generated plans/tool calls are proposals. Yuki policy, identity, approvals, sandboxing and audit decide what executes.
9. **Portable by design.** Source, configs, manifests, evaluation definitions and migration code live in Git. User data, model weights, secrets, checkpoints and runtime databases are backed up separately and never committed by default.
10. **Honest capability reporting.** Discovery, metadata, model downloads, inference, fine-tuning, evaluation and activation are separate states. A candidate is not "trained" or "ready" merely because it appears in a catalog.

## Brain architecture

```text
Interfaces: Desktop | CLI | Voice | Web | Mobile
                    |
             Yuki Companion
       identity | persona | dialogue state
                    |
           Native Intelligence Core
   context builder | planner | reasoning loop
   specialist delegation | critique | reflection
                    |
             Yuki Model Runtime
   local model(s) | adapters | tokenizer | tools
   optional external provider adapters (off by default)
                    |
           Knowledge & Memory Plane
  user memory | session memory | knowledge base
  document ingestion | retrieval | provenance
                    |
           Guarded Capability Plane
   typed tools | skills | approvals | audit
   isolated workers | task queue | recovery
                    |
           Learning & Improvement Plane
 datasets | curation | training | eval | registry
   model cards | release gates | rollback
```

### Ownership boundaries

- **Model runtime:** loads and serves local model artifacts; reports exact model/tokenizer/runtime versions, context limits, quantization and hardware requirements.
- **Intelligence core:** owns message normalization, system identity, context budgeting, model calls, tool proposal parsing, bounded multi-step reasoning, cancellation and turn lifecycle.
- **Memory plane:** stores explicit user memories and conversation/session state separately from general knowledge and model-training corpora. Supports inspect/export/delete and retention policies.
- **Knowledge plane:** ingests documents/web sources with provenance and citations; retrieval is permission-filtered and treats retrieved content as untrusted data.
- **Agent workforce:** specialist agents are role/configuration profiles over shared runtime and guarded tools—not 100 independent foundation models. Each agent has explicit scope, budget, allowed tools and evaluation.
- **Learning plane:** reproducible data pipeline, training jobs, experiment tracking, artifact registry, model evaluation, signed promotion and rollback.
- **Provider adapters:** optional transport compatibility. They do not own Yuki's identity, memory, planner, approval system or data.

## Hugging Face strategy

Use the Hugging Face Hub as a source and distribution channel, not as an automatic trust authority.

For every candidate model:
1. Discover metadata; record immutable repository revision/commit.
2. Read the official model card, license, intended use, restrictions and training-data disclosure.
3. Verify whether the license permits the intended private/commercial/redistribution use.
4. Inspect architecture, tokenizer, context window, supported inference engines, quantization and hardware needs.
5. Review provenance, security advisories and remote-code requirements. Do not enable remote model code by default.
6. Download only to a quarantine/cache directory, pinning the revision and verifying hashes.
7. Run offline loading and smoke tests in a restricted environment.
8. Evaluate on Yuki's versioned capability, safety, privacy, instruction-following and tool-use benchmarks.
9. Record a signed review decision. Activation is a separate user-approved action.

Datasets require the same provenance/license review. A dataset card is useful evidence, not a substitute for reading the actual license and terms. Reject unknown/restricted sources from training until reviewed. Do not train on user memories, private conversations or repository contents by default.

## Training ladder

Do not start with random-initialization pretraining. The compute and data requirements are much larger than fine-tuning and would delay a usable Yuki.

1. **Inference foundation:** run a small, permissively licensed open-weight instruct model locally. Keep it replaceable.
2. **Yuki identity and behavior:** versioned system identity, dialogue policy, structured prompts and deterministic evaluation. Avoid teaching personality by mixing private user data into weights.
3. **Retrieval:** build a private, permission-aware knowledge base and memory retrieval. Update knowledge without retraining weights.
4. **Supervised fine-tuning (SFT):** curate high-quality, licensed examples for Yuki's target tasks; begin with LoRA/QLoRA adapters and keep the base model immutable.
5. **Preference optimization:** only after reliable human preference labels and safety evaluation exist; use an audited method and holdout sets.
6. **Specialist adapters:** train focused adapters for coding, research, planning, voice or other measured gaps; route only when evaluation demonstrates benefit.
7. **Native small models:** train compact classifiers/rankers/routers/wake-word or intent models from scratch where feasible and useful.
8. **Foundation pretraining:** a long-term research project requiring large-scale clean licensed corpora, tokenizer/model design, distributed GPU compute, training stability, evaluation and a sustained budget. Do not promise this before feasibility analysis.

## Initial runtime target

- Local development baseline: Ollama adapter already exists in `providers/ollama.py`; keep it as the first local execution backend.
- Introduce a Yuki-owned `ModelRuntime` contract that supports load/health/generate/stream/cancel/token-count/metadata without leaking backend-specific types.
- Later add a production serving backend (for example, vLLM for supported GPU deployments and llama.cpp for portable quantized CPU/GPU deployments) behind that contract. Select only after hardware profiling and compatibility tests.
- Default provider selection must be local-first and must not require `OPENAI_API_KEY`, `HF_TOKEN` or any other external key.
- Keep Hugging Face Inference Router evaluation/discovery separate from local inference. Existing HF evaluation is a benchmark service, not Yuki's own brain.

## First release milestones

### B0 — Native brain contract
- Define backend-neutral model runtime and model artifact manifest.
- Define local-first startup and explicit optional-provider consent.
- Add a local-model setup/health flow and deterministic no-cloud tests.

### B1 — Useful local companion
- Local chat, streaming, cancellation and persistent conversation.
- Stable Yuki identity/persona and session context.
- Memory inspect/approve/delete/export and permission-aware retrieval.

### B2 — Agentic core
- Bounded planner/executor/reflector loop.
- Typed tool proposals and schemas.
- Approval, audit, idempotency, budgets and cancellation.
- Specialist agents share the same model runtime and guarded tool plane.

### B3 — Knowledge and multimodal
- Provenance-preserving document ingestion and retrieval.
- Speech input/output and wake-word only after privacy and local processing controls.
- Image/audio/video capabilities only when selected local models support them.

### B4 — Yuki learning lab
- Dataset manifest, license/provenance scanner, deduplication and PII review.
- Reproducible training recipes, adapter checkpoints and experiment metadata.
- Offline benchmark suite, regression/safety gates, model registry and rollback.

### B5 — Native model research
- Train small task-specific models first.
- Evaluate compute, data, legal and operational feasibility before foundation pretraining.

## Definition of "independent"

Yuki is independently usable when a clean install can run a supported local model, hold a conversation, maintain user-controlled memory, retrieve local knowledge, use guarded local tools, and recover from restart—with all external provider integrations disabled and no external API credentials present.

Yuki is not independent merely because a local endpoint exists while its planner, memory, identity, tools or key workflows require a cloud provider.

## Existing repository assets and gaps

Already present on main:
- Ollama local provider and structured chat/tool proposal adapter.
- Provider catalog and routing; some provider defaults still need local-first correction.
- Hugging Face metadata discovery and scheduled scouting.
- HF Router model evaluation and local model evaluation registry.
- Starter model benchmark.
- Companion memory store and lexical retrieval.
- A sequence of open, stacked model-control-center and memory PRs (#467–#471).

Gaps:
- No backend-neutral native model runtime contract with complete lifecycle/streaming/cancellation semantics.
- No robust model artifact manifest/cache integrity/activation lifecycle.
- HF discovery/evaluation is not an offline training pipeline.
- No reproducible Yuki-owned SFT/LoRA training and promotion pipeline.
- Current memory retrieval is not yet a unified, production-grade permission-aware knowledge plane.
- Local-first defaults and no-cloud acceptance tests must be made explicit.
- Specialist agent catalog is not evidence of 100 independently intelligent agents; they should share the native brain.

## Release gate

Do not call Yuki a self-trained foundation model or production-ready independent assistant until the independent-use definition is demonstrated by clean-install tests with network egress blocked (except explicitly selected local endpoints), no API keys, and repeatable local model evaluation.
