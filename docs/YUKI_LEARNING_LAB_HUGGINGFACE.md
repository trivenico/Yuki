# Yuki Learning Lab — Hugging Face data and model training pipeline

**Status:** design specification; no training job is claimed to have run.

## Goal

Create a reproducible, auditable pipeline that turns reviewed Hugging Face model and dataset candidates into locally runnable, evaluated Yuki model artifacts. Training is optional and separate from everyday Yuki inference.

## Hub asset intake

### Model candidate record

Store:
- Hub model ID and immutable commit/revision.
- Model-card snapshot hash and retrieval timestamp.
- Declared license plus reviewer-verified license decision and intended-use scope.
- Architecture, parameter count, tokenizer ID/revision, context length and supported tasks.
- Required libraries and minimum versions.
- Remote-code requirement (default deny).
- Supported inference backends, quantization formats and hardware requirements.
- Weight file inventory, sizes and SHA-256 hashes.
- Security/provenance review, reviewer, decision and expiration/re-review date.

### Dataset candidate record

Store:
- Hub dataset ID and immutable revision.
- Dataset-card snapshot hash and actual license/terms review.
- Source URLs, creator, collection date and intended use.
- Languages, domains, splits, sample count and estimated tokens.
- Data schema/features and content hashes.
- PII/sensitive-data scan status, safety review and known exclusions.
- Deduplication method and overlap against evaluation/test corpora.
- Transformations, filtering, synthetic augmentation provenance and lineage.
- Reviewer decision and permitted training purpose.

### Hard rules

- Unknown license, unclear redistribution/training rights, unreviewed terms or missing provenance => quarantine, not train.
- Never execute repository code or dataset scripts from Hub by default.
- Pin revisions; do not train against moving `main` or `latest`.
- Avoid committing tokens, downloaded weights, raw datasets, checkpoints or private user data.
- User memory and conversations are excluded unless the user explicitly opts into a clearly scoped data contribution flow.
- Maintain a clean holdout benchmark that is never used for training or hyperparameter selection.

## Data processing stages

```text
Hub discovery
    -> quarantined immutable snapshot
    -> license/provenance review
    -> malware/archive/path and format checks
    -> PII/sensitive-data policy
    -> normalization + deduplication
    -> quality filtering + contamination checks
    -> reviewed train/validation split
    -> tokenization with pinned tokenizer
    -> signed dataset manifest
    -> training
    -> evaluation + red-team
    -> model registry
    -> human-approved promotion / rollback
```

Each stage emits a machine-readable manifest with input hashes, output hashes, tool versions, configuration, timestamps and decision evidence. Failed or incomplete stages cannot be promoted.

## Training approach

### Stage 1: supervised fine-tuning

Use Hugging Face Transformers Trainer for a transparent baseline. For constrained hardware, start with PEFT LoRA or QLoRA rather than updating every base-model parameter. Keep base model immutable and store adapter weights separately. Use TRL only when its trainer provides a measurable advantage for the selected task.

Training run must pin:
- Base model ID + immutable revision + weight hashes.
- Tokenizer ID/revision and tokenizer hashes.
- Dataset IDs/revisions + dataset manifest hashes.
- Transformers, Datasets, PEFT, TRL, PyTorch/CUDA and driver versions.
- Training script Git commit and configuration hash.
- Seed, precision, batch size, gradient accumulation, sequence length, optimizer, scheduler and steps.
- Hardware type, memory, runtime, energy/cost where measurable.
- Checkpoint hashes and final adapter hash.

### Stage 2: preference tuning

Do not begin DPO/other preference optimization until Yuki has:
- lawful and consented preference labels;
- high-quality chosen/rejected examples;
- user-data separation and deletion controls;
- a fixed holdout set;
- safety and reward-hacking evaluations;
- a clear rollback baseline.

### Stage 3: from-scratch small models

Start with narrow models (intent classifier, routing ranker, wake-word detector) where training data and evaluation are tractable. Full language-model pretraining is a separate feasibility project.

## Evaluation and promotion

Every candidate must pass:
- Base-model regression benchmark.
- Yuki instruction/identity adherence.
- Factuality and grounded retrieval checks.
- Tool-call schema validity and tool-use success.
- Prompt-injection and refusal/security suite.
- Privacy memorization and data-extraction tests.
- Language coverage, latency, memory and throughput checks.
- Human review of representative and failure examples.
- License and distribution review for base, adapter and datasets.

Promotion creates an immutable model release record and a reversible pointer change. Keep previous known-good artifact available. Do not hot-swap a model while sessions are active without an explicit compatibility/drain strategy.

## Hugging Face implementation references

- Transformers Trainer fine-tuning: https://huggingface.co/docs/transformers/main/training
- PEFT integration: https://huggingface.co/docs/transformers/peft
- TRL PEFT integration: https://huggingface.co/docs/trl/peft_integration
- Model cards: https://huggingface.co/docs/hub/model-cards
- Dataset cards: https://huggingface.co/docs/datasets/dataset_card

The official Transformers guide describes fine-tuning as continuing training from pretrained weights and notes it needs substantially less compute/data/time than pretraining from random weights. PEFT trains small adapter parameters while keeping the base frozen; this is the practical starting point for Yuki's first learning runs.

## Initial engineering backlog

1. Implement versioned model artifact and dataset manifest schemas.
2. Add a quarantine-only Hub snapshot downloader with revision pinning, size limits, archive traversal defense and SHA-256 verification.
3. Add license/provenance review records and a deny-by-default policy.
4. Add a dataset validation/normalization pipeline with PII checks, deduplication and contamination reporting.
5. Add a reproducible LoRA SFT recipe with tiny fixture data for CI (no GPU/network).
6. Add opt-in GPU training profiles and checkpoint/resume.
7. Add benchmark harness and release registry with approval/rollback.
8. Add a clean-room local inference acceptance suite with all external providers disabled.

## Resource reality

Fine-tuning a small open-weight model can be practical on a single suitable GPU, especially with quantization and LoRA; exact feasibility depends on model size, context length, batch size, precision and available VRAM. Full foundation pretraining needs much larger compute, data engineering and budget. Do not select a model or promise a training timeline until the target hardware and deployment constraints are inventoried.
