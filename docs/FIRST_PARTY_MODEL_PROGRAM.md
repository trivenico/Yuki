# Yuki First-Party Model Program

Yuki's long-term target is a model family owned and released by the Yuki project. Production inference must not require Ollama, a third-party model API, or a Hugging Face account. Research resources can help build the pipeline, but the release provenance policy distinguishes original Yuki weights from third-party base checkpoints.

## Product commitment

- Train Yuki-Core from random initialization under the Yuki project.
- Maintain Yuki-owned tokenizer, corpus manifests, training code, evaluations, checkpoints, and release metadata.
- Hugging Face is an optional dataset/research/distribution source, never a runtime inference dependency.
- Kaggle is an optional experimentation/training environment. Reproducible training must also run on compatible self-managed compute.
- Oracle Cloud Linux is the always-on inference/orchestration target. GPU inference needs a suitably sized GPU instance; CPU instances can serve smaller quantized releases with lower throughput.
- GitHub is the source of truth for code, configs, dataset manifests, evaluation definitions, and release metadata. Store large checkpoints in artifact storage with immutable hashes, not Git.

## Planned model family

| Model | Responsibility | Initial direction |
|---|---|---|
| Yuki-Core | General language and dialogue | Compact decoder-only transformer trained from random initialization |
| Yuki-Planner | Task decomposition and structured plans | Yuki-Core post-training with strict schema evaluations |
| Yuki-Embed | Local semantic representations | Separately trained compact embedding model |
| Yuki-Rank | Memory/document reranking | Compact relevance model |
| Yuki-Vision | Images and desktop-frame understanding | Later multimodal model with explicit capture controls |
| Yuki-Speech | Speech recognition and synthesis | Separate speech models and evaluations |

This is a roadmap, not a claim these models currently exist.

## Training and provenance policy

1. Record dataset source, owner, license, collection date, allowed purpose, filtering, deduplication, PII handling, and retention.
2. Do not train on data without compatible rights and licensing. Public availability does not mean unrestricted use.
3. Start Yuki-Core from random initialization. A third-party base checkpoint cannot be relabeled as a first-party foundation model.
4. Track tokenizer, architecture, seeds, dataset snapshot hashes, code commit, framework versions, hardware, optimizer, scheduler, and checkpoint hashes.
5. Use held-out and adversarial evaluations; prevent test-set leakage.
6. Publish model cards with provenance, intended use, limitations, risks, and reproducibility evidence.
7. Sign release manifests and verify hashes before production loading.

## Compute reality

Kaggle is useful for data preparation, prototypes, compact-model training, and evaluation within session/accelerator quotas. It does not guarantee uninterrupted large-scale training. A capable general-purpose model trained from scratch requires substantial GPU time, high-quality tokens, evaluation compute, and iterative post-training. Oracle Cloud GPU availability and cost depend on region, quota, instance type, storage, and budget.

Start with a compact reproducible model. Scale only when measured data quality, compute, latency, and evaluation justify it. Training loss or parameter count alone does not establish assistant quality.

## Runtime boundary

Production loads only explicitly installed local Yuki releases. It does not download weights on demand or silently switch to a third-party provider. If a Yuki model is missing, corrupt, or incompatible, model-dependent features report unavailable while deterministic local functions continue.

`core/model_registry.py` verifies manifest schema, first-party provenance policy, root-contained artifact paths, and SHA-256 hashes. It does not yet load tensors, run inference, train weights, sign manifests, or verify model quality. Existing `providers/model_registry.py` is a separate benchmark/evaluation registry, not an artifact loader.
