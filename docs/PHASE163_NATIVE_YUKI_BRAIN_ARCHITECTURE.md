# Phase 163 — Yuki Native Brain: architecture and ownership boundary

**Status:** architecture definition and integration roadmap  
**Repository baseline:** `feature/reasoning-context-builder`  
**Product objective:** Yuki remains useful without any third-party AI API key. Local model execution is the default; external providers are optional adapters.

## What "our own AI" means

Yuki must own the product-level intelligence system: authenticated identity, policy, context construction, memory, planning, tool authorization, agent coordination, evaluation, learning approvals, model selection and lifecycle. These remain Yuki-controlled regardless of which inference backend is selected.

Owning this system is distinct from owning a foundation model's neural weights. Yuki can initially run permissively licensed local models, then fine-tune or distill eligible base models and eventually train Yuki-branded checkpoints. Each model must carry provenance, license, dataset lineage, architecture/runtime compatibility, evaluation report and a rollback path. Do not claim a model is trained by Yuki unless its training run and checkpoint lineage are recorded.

## Verified building blocks already present

- `providers/ollama.py`: local Ollama chat adapter. It defaults to loopback HTTP, requires HTTPS for non-loopback endpoints, and does not silently route to cloud.
- `providers/agent_adapter.py` and `providers/reasoning_agent_adapter.py`: adapter contracts between providers and Yuki's bounded agent loop.
- `core/agent_execution_loop.py`: bounded model/tool/model loop. Tool proposals are passed to the guarded structured tool protocol; the model does not execute tools itself.
- `core/reasoning/context_builder.py`: bounded context construction from authorized memory, keeping retrieved evidence marked as untrusted reference data.
- `providers/reasoning_runtime.py`: owner-scoped loop factory that builds a fresh reasoning loop per authenticated owner.
- `core/intelligence_orchestrator.py`: model-proposed bounded specialist DAG; local validation and coordinator authorization remain authoritative.
- `providers/model_registry.py`: sanitized evaluation registry and manual-review recommendation policy; not an automatic model trainer or activator.
- `providers/ollama_model_inventory.py` and `providers/hardware_snapshot.py`: read-only model inventory and hardware estimates.

These are real foundations, but they do not yet constitute a Yuki-trained foundation model or a fully integrated multimodal companion. Provider support and modules must not be presented as verified live hardware/provider end-to-end functionality.

## Non-negotiable design boundaries

1. **Local-first, no silent cloud fallback.** No API key is required for the local path. If a local model is unavailable, report the missing model/runtime and offer an explicitly configured alternative; never transmit private prompts to a cloud provider implicitly.
2. **Yuki owns policy and identity.** Providers generate proposals only. Authorization, approval, memory access and tool execution stay in Yuki.
3. **Memory is evidence, not policy.** Retrieved memories, web pages, skills, tool output and model output are untrusted. They cannot rewrite system policy or grant permissions.
4. **Learning is staged.** Feedback can create evaluation examples and candidate knowledge. Promotion, fine-tuning and model activation require explicit review, tests and rollback.
5. **Reproducible model lineage.** Track base model digest, license, source, dataset version, preprocessing, training code/config, seed, hardware/runtime, checkpoint digest and benchmark results.
6. **No false autonomy.** Proactive work must be bounded by user-approved schedules, permissions, budgets, cancellation and audit.
7. **Quantum claims require evidence.** Quantum-inspired optimization and probabilistic simulation can run on classical hardware. Genuine quantum execution requires a quantum processor or an explicitly selected quantum service and must report which backend was used.

## Target internal contracts

The next implementation increments should converge on these typed boundaries:

- `BrainRequest`: request ID, authenticated owner, agent ID, task intent, deadline, privacy mode, context/token budget and cancellation signal.
- `BrainContext`: trusted policy/preferences in separate fields from untrusted retrieved evidence, plus provenance and bounded history.
- `ReasoningResult`: answer/proposal, structured plan, model identity, usage, finish reason and validation status.
- `AgentRun` / `AgentStep`: durable state, version, deadline, checkpoint reference and approval interrupt.
- `ModelInvocation`: provider/model digest, capability, timeout, token budget, privacy classification and redacted outcome.
- `LearningCandidate`: source evidence, consent/purpose, license/provenance, evaluator results and explicit review state.

Contracts must not carry raw secrets in event logs. Token accounting must support providers that do not expose exact token counts; report estimates as estimates.

## Model development ladder

### A. Independent operation now
- Install a supported local model using a user-visible setup flow.
- Verify model digest, license metadata, hardware fit and runtime availability.
- Run the Yuki-owned context, policy and tool loop through Ollama.
- Exercise offline mode with network egress disabled.
- Keep cloud providers disabled unless explicitly configured.

### B. Yuki-specific adaptation
- Build a curated, licensed and consented dataset from synthetic tasks, owner-approved examples and eligible public datasets.
- Deduplicate, redact secrets and personal data, preserve source/license lineage and split train/validation/test before training.
- Evaluate baseline before fine-tuning.
- Fine-tune only a compatible model whose license permits the intended use.
- Compare against the unchanged baseline on capability, safety, privacy, latency and resource metrics.
- Require owner/operator approval before installing or activating a candidate checkpoint.

### C. Yuki-owned checkpoints
- Define a model architecture and training recipe only when compute/data/evaluation budgets are credible.
- Record complete lineage and reproducible checkpoints.
- Run adversarial and regression evaluation, including prompt injection, tool misuse, memorization and data-extraction tests.
- Support staged rollout, canary comparison and immediate rollback.
- Clearly distinguish "Yuki fine-tuned model" from "Yuki-trained from scratch model."

## Acceptance criteria

1. A clean machine with Ollama and a supported installed model can run the core companion path with no external API credentials.
2. Offline mode proves no network egress from the local inference path and does not silently switch providers.
3. A provider swap does not alter authorization, memory scope, tool permissions or approval requirements.
4. Model and training artifacts have verifiable hashes, provenance and license status.
5. Every learning candidate is evaluated against a fixed versioned benchmark and can be rejected without changing the active model.
6. Model activation is explicit, versioned, observable and reversible.
7. End-to-end tests cover timeout, malformed model output, unavailable local runtime, cancellation, resource exhaustion and prompt injection.

## Explicit non-goals for this phase

- Do not train a foundation model from scratch before establishing compute, data rights and baseline evaluation.
- Do not call a local model "quantum AI."
- Do not let model output directly mutate memory, permissions, policies, provider configuration or code.
- Do not treat a model catalogue, downloaded checkpoint or fine-tuning script as proof of an owned model.

## Immediate next engineering sequence

1. Fix and re-run current cross-platform CI regressions in the durable operation tests.
2. Add a clean-install local-only acceptance test around the existing owner-scoped Ollama runtime.
3. Add provider privacy routing assertions: local-only requests must never invoke cloud adapters.
4. Introduce versioned model invocation metadata and redacted trace events.
5. Add a reproducible benchmark corpus for Yuki-specific tasks.
6. Only then implement the dataset and fine-tuning pipeline.
