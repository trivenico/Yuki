# Phase 170 — Yuki pretrained-model adaptation program

**Status:** strategy and governance contract  
**Primary objective:** build Yuki's own model family by adapting suitable, already-pretrained open-weight models and curating lawful, high-quality datasets. Training a foundation model from random initialization is explicitly out of scope for the current roadmap.

## 1. Product and ownership decision

Yuki starts from pretrained base models selected through evidence-based discovery, compatibility and license review. The Yuki team builds distinctive capability through curated/versioned datasets, supervised fine-tuning using LoRA/QLoRA, retrieval-augmented generation, and Yuki-owned identity, reasoning, planning, memory and tool orchestration.

A Yuki-adapted checkpoint is a Yuki-maintained artifact derived from an identified upstream base. It must not be described as trained from scratch or as wholly original foundation weights. Preserve upstream attribution and comply with model and dataset terms.

## 2. Pretrained model acquisition

Candidate sources may include Hugging Face, official model publishers, academic repositories and other open-weight ecosystems. Discovery metadata helps find model cards, architecture, tokenizer, quantization, framework support and revisions; metadata alone is not a model or training dataset.

For every candidate, record immutable repository/revision, source URI, exact artifact digest, publisher, architecture, tokenizer, context limits, hardware needs, model card, license identifier and evidence, fine-tuning rights, commercial-use rights, redistribution constraints and required attribution. Do not infer rights from "open source", "public", "open weights" or from a platform listing. Review every model license and dataset license independently.

Quarantine downloads. Verify hashes and safe archive paths. Never execute repository-provided custom Python, install scripts or remote code by default. Prefer trusted formats and pinned, reviewed runtime dependencies.

## 3. Dataset programme

Potential sources include permissively licensed Hugging Face datasets, public datasets with explicit compatible terms, curated demonstrations, owner-approved synthetic examples, and approved teacher-generated targets where teacher terms permit training use. Track dataset source/revision, license, provenance, consent, privacy, preprocessing lineage, content hashes, deduplication, contamination checks and immutable train/validation/test splits.

Open-source metadata is not a substitute for actual training examples. Public availability does not automatically grant training, commercial-use, redistribution or personal-data rights. Never train on raw private conversations by default; memory and training data are separate systems unless a specific informed and revocable consent process authorizes use.

## 4. Adaptation strategy

1. Evaluate several pretrained candidates against a fixed Yuki capability benchmark before adaptation.
2. Select the smallest capable base that meets quality, latency, memory and license constraints.
3. Establish a baseline with the exact model digest, tokenizer, runtime and quantization.
4. Start with LoRA/QLoRA supervised fine-tuning; full fine-tuning is an exceptional, separately budgeted path.
5. Use retrieval for changing factual knowledge rather than repeatedly baking all knowledge into weights.
6. Compare the adapted candidate against the unchanged base and prior Yuki release on held-out evaluations.
7. Keep adapters and base weights separately versioned when possible; package a derived model only through a reproducible, licensed process.

No from-scratch foundation-model training runner is part of the current roadmap.

## 5. Yuki identity and self-improvement

Yuki's distinctive intelligence is not only its weights. The Yuki brain owns identity, policies, memory permissions, context construction, planning, specialist orchestration, tool authorization, learning proposals, evaluation and release decisions. Base-model output is untrusted input to this control plane.

"Self-learning" is a controlled improvement loop: capture privacy-safe, consented feedback and evaluation signals; propose memory or knowledge updates separately from weight updates; curate candidate training examples with provenance and human review; run experiments against immutable data and benchmarks; require measurable improvement and safety non-regression; and require human approval before releasing an adapter or model.

Models cannot autonomously approve their own data, permissions, training runs or releases. No silent cloud fallback or upload of private user context to third-party services.

## 6. Model family and runtime

Yuki Core is the primary conversational/reasoning model target. Additional components may include embeddings, reranking, code, vision, speech and narrow specialist adapters. Ollama is an initial local inference runtime, not the model itself. Optional external APIs can be used for explicitly approved comparisons or specialist tasks; they are not required for local operation.

## 7. Release evidence

Each Yuki-adapted release records base model digest and license evidence, dataset manifest IDs and split digests, training code revision, method, seed, hyperparameters, hardware/runtime, checkpoint hashes, evaluation report, attribution and approval. Lifecycle: discovered → quarantined → rights-reviewed → evaluated baseline → adapted → evaluated → approved → canary → active → retired.
