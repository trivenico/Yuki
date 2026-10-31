# Hugging Face upstream integration audit

## Decision

Reuse upstream packages as versioned dependencies first. Do not bulk-copy entire repositories into Yuki. Upstream code changes rapidly, and copying it wholesale creates security, attribution, maintenance, dependency and license-update burdens. If a small source file genuinely must be adapted, preserve its copyright/license headers, include the complete applicable license and NOTICE files, record the exact upstream repository and commit, and document local modifications.

## Recommended integration matrix

| Upstream project | Yuki capability | Integration strategy | Priority |
|---|---|---|---|
| huggingface/transformers | Model architecture, loading, generation, multimodal | Optional runtime dependency behind Yuki model-provider interface | P0 |
| huggingface/huggingface_hub | Model/dataset metadata and artifact transfer | Use Hub APIs; never execute remote model code by default | P0 |
| huggingface/safetensors | Weight serialization | Prefer safetensors; validate files and hashes | P0 |
| huggingface/tokenizers | Fast tokenizer implementations | Use via Transformers unless a direct need exists | P1 |
| huggingface/datasets | Dataset loading, streaming and transforms | Isolated ingestion worker, pinned revisions | P0 |
| huggingface/peft | LoRA/QLoRA adapters | Training worker dependency | P0 |
| huggingface/accelerate | Hardware and distributed training | Training worker dependency | P1 |
| huggingface/trl | SFT, DPO, GRPO and preference optimization | Add after supervised fine-tuning pipeline is evaluated | P1 |
| huggingface/evaluate | Standardized evaluation | Evaluation worker with versioned benchmark suites | P1 |
| huggingface/smolagents | Agent execution and tool abstractions | Optional adapter; preserve Yuki permissions, contracts and sandbox boundary | P1 |
| huggingface/optimum | Hardware-specific inference optimization | Add only for tested target hardware | P2 |
| huggingface/diffusers | Image/video generation | Separate media provider and optional environment | P2 |
| huggingface/transformers.js | Browser inference | Desktop/web capability only when model size and browser support justify it | P2 |

## Installation boundary

The root Yuki CLI/Desktop environment must stay lightweight. `requirements-training.txt` is an optional environment manifest, not a replacement for the base requirements. Install PyTorch separately using the official selector for the target CPU/CUDA/ROCm platform. Pin and lock exact tested versions in CI before production.

## Licensing and provenance requirements

- Review each upstream repository's LICENSE, NOTICE, dependency licenses and release/commit before adopting it.
- Apache-2.0 and MIT source licenses do not automatically grant rights to every model weight, dataset, tokenizer artifact or third-party asset distributed alongside the code.
- Review model-card and dataset-card terms independently; record the exact immutable revision and provenance.
- Preserve upstream notices and copyright attribution for every vendored or modified file.
- Generate an SBOM and run dependency vulnerability/license checks on every release.
- Never set `trust_remote_code=True` by default. Any exception requires code review and isolated execution.
- Treat Hub metadata, model cards, datasets and agent/tool definitions as untrusted input.
- Do not train on imported ChatGPT/Claude/other platform conversation exports until ownership, privacy, consent, third-party content and provider terms have been reviewed. Redact secrets and personal data.

## Pretrained from day one

Yuki should ship with a model-provider abstraction and a tested, user-selectable open-weight model profile. The model weights are downloaded separately by the user or deployment process after license, hardware and checksum checks. A pretrained checkpoint is not the same as a Yuki-trained foundation model. Yuki's own value is the orchestration, tools, memory, policy, evaluation and later adapter fine-tuning that wrap these models.

## Staged delivery

1. Install optional dependencies in a clean training virtual environment and produce a lockfile/SBOM.
2. Implement a Transformers inference adapter behind the existing provider registry.
3. Implement a pinned-revision Hub artifact downloader with checksum, size, disk-space and allowlist controls.
4. Connect Datasets to the approved dataset registry through a restricted ingestion worker.
5. Connect PEFT and Accelerate to the existing resource-aware training queue.
6. Add TRL only after SFT, checkpoint/resume and evaluation are working.
7. Evaluate smolagents in a compatibility spike; do not replace Yuki's existing agent runtime without parity and security tests.

## Acceptance gates

- Base CLI/Desktop installation does not install the training stack.
- No remote code execution on model download or load.
- Model and dataset revisions are immutable and recorded.
- Downloads have byte limits, timeouts, hashes and cancellation.
- Training runs in a restricted worker and cannot access secrets or user files by default.
- Model promotion requires benchmark results, human approval and rollback metadata.
