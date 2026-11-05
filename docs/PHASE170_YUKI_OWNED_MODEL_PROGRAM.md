# Phase 170 — Yuki-owned model program

**Status:** engineering strategy and governance contract  
**Primary objective:** make a Yuki-owned model the default intelligence model while retaining Ollama as a local inference runtime and external model APIs as opt-in integrations.

## 1. Product and ownership decision

Yuki's primary model is intended to be a model family developed and maintained by the Yuki project. The family may begin with a legally compatible open-weight base model that is adapted, evaluated and released as a Yuki checkpoint. Over time, the project may train selected models from scratch when data rights, compute, engineering capacity and measured quality justify doing so.

The product must distinguish these states in its UI, registry and release notes:

- **External base model:** unchanged third-party weights.
- **Yuki-adapted model:** third-party base weights fine-tuned or otherwise adapted under terms that permit the work.
- **Yuki-distilled model:** trained using teacher-generated targets only where the teacher model terms and dataset rights permit that use.
- **Yuki-trained model:** weights produced by a documented Yuki-controlled training run from an identified initialization and dataset.
- **Yuki model family:** a versioned collection of models for different modalities/capabilities, not a claim that one checkpoint does everything.

A model is not Yuki-trained merely because it is downloaded, renamed, wrapped by Ollama, given a Yuki system prompt, or listed in the Yuki registry.

## 2. Runtime and provider hierarchy

1. **Primary:** the approved Yuki model checkpoint, selected by Yuki's model policy.
2. **Local runtime:** Ollama is the initial supported runtime for compatible local checkpoints. Runtime and weights are separate lifecycle objects.
3. **Optional local alternatives:** other explicitly supported runtimes may be added behind provider adapters.
4. **Optional external APIs:** disabled unless configured and explicitly selected for a permitted task. No silent cloud fallback.
5. **Unavailable primary model:** fail with a clear degraded-state message or use a separately approved fallback policy. Never silently transmit private prompts to a remote provider.

Provider output remains an untrusted proposal. Yuki retains control over identity, context assembly, memory access, policy, approvals, tool authorization, execution, model selection and learning promotion.

## 3. Model family roadmap

Build a coherent family incrementally rather than claiming a single model will excel at every task:

- **Yuki Core:** general instruction following, conversation, reasoning and structured output.
- **Yuki Code:** software engineering and repository task specialization.
- **Yuki Embeddings:** semantic representations for Yuki's retrieval and memory systems.
- **Yuki Reranker:** ranking candidate memories and documents.
- **Yuki Vision:** image understanding when a suitable dataset, architecture and runtime are available.
- **Yuki Speech:** speech recognition and speech generation components.
- **Yuki Specialist adapters:** narrow task adaptations (for example planning, extraction or classification) where evaluations demonstrate a benefit.

Each component has its own capability contract, model artifact, dataset lineage, benchmark suite, license review and deployment requirements. The family shares Yuki's authorization and orchestration layer; specialists do not gain independent permissions.

## 4. Data and open-source provenance

Open-source metadata is essential for discovery, compatibility and provenance, but metadata alone is not training data. A model card, architecture config, tokenizer description or dataset catalogue does not supply the underlying examples needed to train useful weights.

For every imported model, dataset, tokenizer, codebase or teacher output, record:

- canonical source and immutable revision/commit;
- artifact and file hashes;
- author/maintainer and source project;
- exact license and relevant usage restrictions;
- whether commercial use, modification, redistribution, fine-tuning and distillation are permitted;
- attribution and notice obligations;
- collection date and retrieval method;
- dataset split and preprocessing lineage;
- known personal-data, sensitive-data and consent constraints;
- security scan and executable-code policy.

Do not assume that "open source", "publicly available", "research", or "open weights" means unrestricted commercial training or redistribution. Review each license and terms independently. Quarantine imported code and model artifacts; do not execute remote repository scripts or custom model code during ingestion by default.

## 5. Dataset preparation and governance

Potential sources, subject to review:

1. permissively licensed public datasets with compatible terms;
2. synthetic examples generated for defined Yuki capabilities;
3. owner-approved and appropriately consented interaction examples;
4. curated task demonstrations and evaluations authored by the Yuki team;
5. teacher-generated synthetic targets where teacher terms permit generation, retention and training use.

Before training:

- define the capability and intended use;
- verify rights, consent, provenance and purpose limitation;
- remove secrets and minimize personal data;
- deduplicate exact and near-duplicate examples;
- detect contamination against evaluation sets;
- split train/validation/test before tuning;
- version raw manifests, preprocessing code and split manifests;
- retain deletion and correction lineage for data that must be removed;
- measure coverage, quality, imbalance and harmful-content risks.

Do not train directly on raw private conversations by default. User feedback is a candidate signal, not automatic permission to train. Keep private memory separate from the model-training corpus unless a specific, informed and revocable consent path exists.

## 6. Training and adaptation ladder

### Stage A — Yuki baseline on local runtime

- Select compatible base checkpoints after license and hardware review.
- Run them through Ollama where supported.
- Establish fixed Yuki-specific benchmarks and a resource baseline.
- Confirm offline execution and absence of silent cloud egress.
- Record exact base model digest, quantization and runtime versions.

### Stage B — Yuki adaptation

- Start with parameter-efficient fine-tuning (for example LoRA/QLoRA) where architecture and license permit.
- Compare against the unchanged base model on the same versioned benchmark.
- Test reasoning, structured output, tool-call schema adherence, retrieval use, refusal behavior, prompt injection resistance, privacy leakage and regressions.
- Merge or quantize only through reproducible, validated steps.
- Label resulting artifacts as Yuki-adapted, not trained from scratch.

### Stage C — Yuki-controlled training

- Define a target architecture and tokenizer only after credible dataset and compute budgets exist.
- Run small-scale experiments and scaling measurements before committing to large training.
- Track seeds, hyperparameters, optimizer, precision, distributed setup, hardware, software dependencies and training data manifests.
- Preserve checkpoints and hashes; make runs reproducible to the extent practical.
- Validate independent held-out sets, memorization, contamination, robustness and safety.
- Do not claim parity with frontier models without comparative evidence.

## 7. Evaluation, promotion and rollback

Every candidate is evaluated against a fixed, versioned suite covering:

- task success and human-rated answer quality;
- groundedness and citation/evidence correctness;
- reasoning consistency and calibration;
- tool schema validity and denied unauthorized actions;
- memory retrieval precision/recall and context use;
- privacy leakage, memorization and data extraction;
- prompt injection and adversarial robustness;
- p50/p95 latency, throughput, token usage and peak memory;
- hardware/runtime compatibility and offline behavior.

Expected-term coverage is only a narrow proxy and cannot be treated as general model quality. Use deterministic metrics where appropriate and human review for qualitative capabilities.

A candidate progresses through: **proposed → provenance/license review → dataset review → trained → evaluated → approved → canary → active → retired**. Rejection must not alter the active model. Activation requires explicit approval, immutable artifact identity, health checks, monitoring and a tested rollback path. No model may promote itself.

## 8. Artifact manifest

Every model release should have a machine-readable manifest containing at least:

- model family, name, version and capability scope;
- artifact SHA-256 and immutable source/base digest;
- base model and adaptation/training type;
- architecture, tokenizer, quantization and supported runtime;
- license and attribution references;
- dataset manifest IDs, versions, split hashes and consent/provenance status;
- training code revision, configuration, seed and hardware profile;
- benchmark suite version and evaluation report hash;
- known limitations, safety evaluation and approval record;
- release timestamp and rollback target.

Never place secrets, raw private prompts, personal data or private training examples in a public manifest.

## 9. Immediate implementation sequence

1. Keep the current Ollama path as the local inference baseline and make provider/model identity explicit.
2. Generalize evaluation contracts so local Ollama and optional API models can run the same synthetic benchmark cases without sharing credentials or routing policy.
3. Version benchmark suites and produce comparable reports with model artifact identity.
4. Add a provenance/license manifest and quarantine workflow for imported model and dataset metadata.
5. Add dataset manifest, deduplication, split and contamination checks.
6. Add a small, reproducible fine-tuning experiment only after the data/license and hardware gates pass.
7. Add candidate checkpoint approval, canary deployment and rollback.
8. Revisit training from scratch after measured adaptation results and a credible compute/data plan.

## 10. Acceptance criteria

- Yuki's default model selection resolves to an approved Yuki-owned artifact when one is available.
- Ollama can run the approved local Yuki checkpoint without any external API key.
- Optional providers remain disabled unless explicitly configured and selected.
- Offline tests prove that local-only requests do not invoke external providers.
- Model and dataset provenance can be verified from immutable manifests and hashes.
- Training and evaluation are reproducible and do not use private data without explicit permission.
- No candidate becomes active without review, approval, monitoring and rollback.
- Product language accurately distinguishes external, adapted, distilled and Yuki-trained models.

## Current reality

This document defines the target program; it does not mean Yuki already owns trained foundation-model weights. The current repository contains local inference integration, reasoning orchestration, memory, evaluation and registry foundations. Building and validating Yuki's own checkpoints is a separate staged engineering programme requiring suitable data, licenses, compute and measured results.
