# Open-source datasets and model-training strategy

## Goal

Accelerate Yuki's first useful release by reusing pretrained open-weight models, permissively licensed code, public datasets with verified terms, mature training frameworks and established evaluation suites. Build Yuki's identity, orchestration, memory, agent runtime, policy, skills, tool execution and product experience ourselves.

Do not train a foundation model from scratch for the MVP. Start with inference + retrieval + skills + durable workflows. Add fine-tuning only when evaluation demonstrates a repeatable capability gap that cannot be solved more cheaply with routing, prompting, retrieval or tools.

## Reuse map

| Capability | Candidate upstreams | Yuki-owned responsibility |
|---|---|---|
| Transformer architectures and pretrained model loading | PyTorch, Hugging Face Transformers | Model contracts, routing and release policy |
| Dataset loading and processing | Hugging Face Datasets, Apache Arrow | Provenance, consent, license gates and versioning |
| Parameter-efficient fine-tuning | PEFT, LoRA, QLoRA, TRL | Job orchestration, dataset approval, evaluation and rollback |
| Local inference | Ollama, llama.cpp | Local model selection and runtime integration |
| Self-hosted serving | vLLM | Deployment, isolation, access control and observability |
| Embeddings and reranking | Sentence Transformers, compatible rerankers | Versioned indexes and retrieval quality evaluation |
| Vector retrieval | FAISS, Qdrant | User data model, tenancy, deletion and source citations |
| Quantization | GGUF, bitsandbytes | Hardware-aware selection and quality/latency gates |
| Speech and vision | Whisper, Piper, OpenCLIP and compatible VLMs | Explicit device permissions and multimodal task policy |
| Evaluation | lm-evaluation-harness and curated task suites | Yuki-specific regression, safety and release gates |

A project being open source does not make every model checkpoint or dataset it distributes open source. Verify code license, model license, dataset license, training-data provenance, commercial-use restrictions, attribution, privacy obligations and gated-access terms separately.

## Dataset discovery starter list

The registry includes discovery leads, not approved training corpora:
- `OpenAssistant/oasst1`: instruction/dialogue research.
- `openai/gsm8k`: math reasoning.
- `google-research-datasets/mbpp`: code generation.
- `truthfulqa/truthful_qa`: truthfulness evaluation.
- `HuggingFaceH4/ultrafeedback_binarized`: preference-learning research.

These are not pre-certified as license-compatible, private, clean, deduplicated or suitable for commercial training. Inspect the exact dataset card, upstream component lineage, pinned revision, splits, personally identifying content, benchmark contamination and applicable terms before use. Keep evaluation/test splits out of training.

## Dataset approval contract

`learning/dataset_registry.py` provides a metadata-only starter catalogue and a fail-closed approval record. A candidate is never downloaded or trained automatically. Training approval requires:
- Exact Hugging Face dataset repository and immutable 40-character commit revision.
- Exact license identifier from the pinned revision and HTTPS dataset-card evidence.
- License in the initial policy allowlist; all other licenses require separate legal review and policy change.
- Documented provenance review, privacy review and security review.
- Named human approval.

Passing this gate means approved for a separate ingestion review, not that ingestion has occurred or that a training run is authorized. The ingestion pipeline must still scan files, verify hashes, enforce size/type limits, deduplicate, redact or exclude sensitive data, record lineage, and preserve source notices.

## Neural network and training strategy

1. **MVP:** inference with suitable pretrained models; no weight training required.
2. **Knowledge:** retrieval, memory and curated skills improve task performance immediately.
3. **Evaluation:** build a versioned suite for coding, reasoning, instruction following, tool use, factuality, refusal, prompt injection and task completion.
4. **Dataset curation:** blend legally approved public datasets with user-consented, human-reviewed examples. Keep benchmark evaluation data isolated.
5. **Fine-tuning:** use PEFT/LoRA/QLoRA on a compatible model and hardware. Begin with small, narrow experiments.
6. **Promotion:** compare base vs adapter on held-out quality, safety, regression, latency and memory. Version, sign, stage and keep rollback.
7. **Foundation model research:** defer pretraining from scratch until Yuki has a clear data, compute, research and funding plan.

Error correction should initially be implemented as software around model outputs: structured schemas, constrained decoding where available, static analysis, compiler/type checks, unit tests, source validation, independent critic/verifier passes and retry budgets. Do not assume a generic neural error-correction model is the right solution for every failure class.

## Resource policy

Local processing avoids external API token billing and provider quotas, but remains bounded by RAM/VRAM, storage, electricity and compute time. Interactive tasks take priority. Background training is opt-in, pausable, checkpointed, resource-capped and scheduled when the machine is idle or on external power according to user policy. Never imply free local compute is unlimited.

## MVP speed plan

- Ship the existing provider + task runtime + guarded skills + CLI loop first.
- Add local model setup and hardware detection.
- Add document retrieval and conversation import.
- Add dataset registry, approval, ingestion and evaluation.
- Add optional LoRA experiments only after a measurable baseline exists.
- Integrate Desktop against the same runtime API.
- Keep external providers optional, not required.

## Release gate

A Yuki MVP can be useful without custom weights. Production claims require end-to-end tests, security/privacy review, resource limits, backup/restore, model/dataset provenance, reproducible evaluation and a working rollback path.
