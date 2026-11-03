# LongCat-2.0 research and Yuki model strategy

Research date: 2026-11-03

## Executive decision

Study LongCat-2.0 as an architecture and systems reference. Do not make it Yuki's default local model, training base, or a required dependency.

Official source: https://github.com/meituan-longcat/LongCat-2.0

The public repository currently presents a model release/model card, figures, README, and MIT license. It documents deployment through SGLang on GPU and an NPU-specific stack, but it does not publish a complete reproducible pretraining pipeline or the infrastructure used for the reported training run. Re-check the upstream repository and model card before any adoption decision.

## What LongCat-2.0 reports

- 1.6 trillion total parameters, with approximately 48 billion activated per token.
- Mixture-of-Experts (MoE) architecture.
- More than 35 trillion pretraining tokens and millions of accelerator-days.
- Long-context training with 1M-context data.
- LongCat Sparse Attention (LSA): Streaming-aware Indexing, Cross-Layer Indexing, and Hierarchical Indexing.
- N-gram Embedding and three-step Multi-Token Prediction.
- Agentic/coding workflow integration and a documented function-tool chat template.
- Model weights released under MIT terms, subject to reviewing the exact model artifacts and any separate assets/dependencies.

These are upstream-reported specifications, not independently reproduced Yuki benchmarks.

## Adoption matrix

| Upstream idea | Yuki decision | Practical implementation |
|---|---|---|
| MoE expert specialization | Research, do not copy architecture prematurely | Keep provider/model routing capability-aware; later prototype sparse specialist models only with training data, compute, and benchmark evidence |
| Long-context capability | Adopt the product pattern now | Hierarchical memory, retrieval, conversation summaries, context budgets, and source-linked recall |
| Sparse attention | Research only | Benchmark supported long-context models first; custom attention kernels require model-level training and hardware-aware engineering |
| N-gram embeddings | Research only | Do not add to current inference stack; evaluate only as part of a controlled model-training experiment |
| Multi-token prediction | Research for inference optimization | Benchmark speculative decoding only where the selected runtime and model support it |
| Structured tool calling | Adopt now | Normalize tool schemas and calls across providers; validate arguments, enforce permissions, record results, and verify completion |
| Long-horizon agent behavior | Adopt the engineering principles | Durable task state, explicit checkpoints, bounded retries, cancellation, audit logs, and human approval for consequential actions |
| SGLang deployment | Optional future serving backend | Add only behind a provider/runtime adapter and only after hardware and model compatibility checks |

## Hardware and feasibility

A 1.6T-parameter model is not a realistic default target for an ordinary laptop or desktop. Even the activated-parameter count does not describe total weight storage, KV cache, memory bandwidth, routing overhead, or serving concurrency. The published training scale is far beyond Yuki's current project resources.

Yuki should instead support a model ladder:

1. Small local models for baseline offline chat and lightweight tasks.
2. Stronger open-weight models for coding, reasoning, and tool use when the host has sufficient RAM/VRAM.
3. Optional remote Hugging Face inference for evaluation or temporary capability expansion.
4. Fine-tuned Yuki-specialist models once we have a curated, consented, licensed dataset and repeatable evaluation.
5. A future Yuki-native foundation model only after compute, data governance, tokenizer, training infrastructure, and measurable capability targets are funded and validated.

The goal is independent Yuki intelligence and ownership of its runtime, memory, tools, data, training recipes, and evaluation—not pretending that a model wrapper is a new foundation model.

## Immediate engineering priorities

### 1. One provider-neutral structured chat contract

Yuki already has provider-level structured chat pieces, including an Ollama chat implementation and a provider-to-agent adapter. Consolidate and test a single contract with:

- Role-preserving messages.
- JSON-schema-like function definitions.
- Normalized tool-call IDs, names, and argument objects.
- Explicit finish reasons and bounded response sizes.
- Streaming events where genuinely supported.
- Provider capability metadata; never infer tool calling or context size from a provider name.
- Conformance tests shared by local and cloud adapters.

Do not silently flatten structured transcripts into one prompt or silently switch to a cloud provider.

### 2. A permission-enforcing agent execution loop

The model may propose a tool call; it must never grant itself permission. The execution path must:

1. Validate the model response envelope.
2. Resolve the tool name against a registered allow-list.
3. Validate arguments against the registered schema.
4. Apply risk policy and request fresh approval for protected actions.
5. Execute with workspace, network, time, output, and resource bounds.
6. Return a bounded tool result to the model.
7. Verify task completion against observable evidence.
8. Persist task state and metadata-only audit events.

Limit tool rounds, total tokens, wall-clock time, tool output, and total task cost. Add cancellation and recovery tests before enabling autonomous multi-step execution.

### 3. Hugging Face model governance

Build a model catalog before enabling model downloads:

- Require an explicit model ID and revision/commit pin.
- Record model card URL, license, source, architecture, supported runtime, quantization, and hardware requirements.
- Require explicit user confirmation before downloading large artifacts.
- Verify file hashes where published and store local artifact manifests.
- Never execute remote model code by default; any trust_remote_code opt-in requires source review and isolation.
- Keep HF tokens in environment variables or OS secret storage, never in config, logs, or Git.
- Treat each model, tokenizer, dataset, adapter, and training corpus as a separate license/provenance review.
- Provide uninstall, cache inspection, and disk-space estimates.

The existing Hugging Face inference evaluation helper is a useful bounded starting point, but remote evaluation is not the same as local model download, training, or independent model ownership.

### 4. Yuki-owned training/evaluation pipeline

Build this incrementally and keep every run reproducible:

- Dataset manifest with provenance, license, consent, language, and filtering metadata.
- PII/secret filtering and deduplication before training.
- Train/validation/test splits with contamination checks.
- Baseline evaluation before fine-tuning.
- LoRA/QLoRA fine-tuning experiments before full parameter training.
- Versioned checkpoints, tokenizer/config snapshots, and training metadata.
- Regression, safety, multilingual, tool-use, and task-completion evaluations.
- Human review and rollback before promoting a model.

Never train on private user conversations by default. Training data must be explicitly opted in, exportable, deletable, and legally usable.

## Open-source reuse policy

Yuki can learn from and reuse open-source code, but every import must be deliberate:

1. Pin the exact upstream repository revision and source file path.
2. Inspect the file header, repository license, bundled assets, dependencies, and transitive licenses.
3. Preserve copyright and license notices; add required attribution and change notices.
4. Prefer reimplementing an interface or algorithm when upstream code is tightly coupled to another runtime.
5. Add a row to THIRD_PARTY_NOTICES.md and the relevant capability audit.
6. Add tests for Yuki's adaptation and security boundaries.
7. Never copy model weights or datasets solely because the repository's source code is MIT-licensed; model/data licenses must be checked separately.

For LongCat-2.0, the MIT model-weight statement is promising, but it does not make the massive model practical to run locally and does not provide the missing training pipeline.

## Acceptance criteria before calling this integrated

- A local model can be selected and used with cloud credentials absent.
- A provider conformance suite passes for each claimed structured-chat capability.
- Invalid, unknown, duplicate, oversized, and unauthorized tool calls are rejected.
- Tool outputs are bounded and treated as untrusted model context.
- Task state survives process restart and never replays ambiguous side effects automatically.
- Model artifacts are revision-pinned and have a recorded license/provenance manifest.
- No secret, private memory, or unapproved user data enters training/evaluation artifacts.
- CI and manual hardware tests support every claimed platform/model combination.

## Sources

- LongCat-2.0 repository and model card: https://github.com/meituan-longcat/LongCat-2.0
- LongCat-2.0 deployment references in the upstream README: SGLang cookbook for GPU and SGLang-FluentLLM for NPU.
- Yuki's existing provider routing guide: docs/PROVIDER_ROUTING.md
- Yuki's existing upstream reuse policy: docs/UPSTREAM_CAPABILITY_AUDIT.md
- Yuki's existing provider evaluation helper: providers/model_evaluation.py