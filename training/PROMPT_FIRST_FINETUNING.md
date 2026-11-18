# Prompt-first model adaptation and fine-tuning gates

## Decision

Yuki must not fine-tune a model merely because a JSONL dataset exists. Start with the existing instruction/system prompt, approved examples, tools, retrieval, and deterministic workflow policies. Fine-tune only when a repeatable evaluation shows a behavior gap that prompting, retrieval, or code-level controls do not solve reliably.

A system prompt can often establish role, tone, response structure, tool-use boundaries, and examples without producing new weights. Keep policy and authorization in code; do not try to train away security requirements.

## Adaptation ladder

Use the least expensive intervention that fixes the measured failure:

1. System/developer prompt and a small, curated set of examples.
2. Better tool schemas, validators, deterministic orchestration, and retrieval.
3. Model/provider selection or inference configuration.
4. Supervised fine-tuning (SFT) with LoRA/QLoRA when repeated, stable behavior gaps remain.
5. Preference optimization only when there is a defensible preference dataset and a separate evaluation plan.
6. Full fine-tuning or continued pretraining only after a documented technical case; these are not the default.

Do not train facts that change frequently into model weights. Put changing knowledge in retrieval and source systems. Do not train permissions, secrets, personal data, credentials, raw voice samples, speaker embeddings, or private conversation logs into a general-purpose checkpoint.

## JSONL conversation format

Use one JSON object per line. The recommended canonical record is a conversation with explicit role/content messages, compatible with common chat-template SFT loaders. Keep the inference chat template identical to the template used during training.

Example (illustrative only; not production training data):

```jsonl
{"messages":[{"role":"system","content":"You are Yuki, a careful desktop assistant. Follow the configured tool permissions."},{"role":"user","content":"Summarize this meeting note in three bullets."},{"role":"assistant","content":"- The team agreed to ...\n- The open risk is ...\n- The next action is ..."}]}
{"messages":[{"role":"user","content":"Can you delete these files?"},{"role":"assistant","content":"I can inspect the selected files and explain the impact first. I will not delete anything without your explicit confirmation."}]}
```

Dataset rules:

- Include high-quality, representative, diverse examples of the intended task, not just idealized happy paths.
- Include ambiguity, refusal, correction, tool failure, permission denial, and safe escalation examples where relevant.
- Do not put executable tool calls in free-form assistant text and assume they will be safe. Tool invocation must remain schema-validated and permission-checked by runtime code.
- Deduplicate near-identical examples; detect train/evaluation leakage; split by conversation, source, or scenario family rather than randomly splitting adjacent turns.
- Preserve provenance, license, consent, and permitted-use metadata for every source dataset.
- Remove credentials, access tokens, personal identifiers, confidential customer data, and private voice data before dataset creation.
- Do not use live user conversations for training by default. Require explicit opt-in, a documented retention/deletion process, and a reviewable data export.
- Keep a frozen, held-out evaluation set that is never used for training or prompt selection.

## Choosing Unsloth or Axolotl

Use one framework per experiment, based on the target model and available GPU environment. Do not make both frameworks mandatory dependencies of Yuki's desktop runtime.

- **Unsloth** is a good first experiment for supported model families and a managed GPU notebook workflow. Its documentation covers LoRA/QLoRA and saving adapters or exported model formats. Check the current model support and installation requirements for the exact base model before starting.
- **Axolotl** is a configurable training pipeline with JSONL/chat-template SFT, LoRA/QLoRA, preprocessing, inference, and adapter merge workflows. Its configuration must match the exact model architecture and chat template.
- Prefer LoRA/QLoRA for the first controlled experiment. The output is commonly an adapter checkpoint, not a complete standalone replacement model. Merging/exporting creates a derived checkpoint and must be treated as a separate reproducible build step.
- Do not assume a JSONL file can be passed unchanged to every recipe. Validate its schema, message roles, chat template, loss masking, sequence length, and special-token handling against the selected framework.
- Use Kaggle or another isolated GPU runner for experiments; never make desktop startup download or train a model.

Official references:
- Unsloth documentation: https://unsloth.ai/docs
- Axolotl dataset formats: https://docs.axolotl.ai/docs/dataset-formats/
- Axolotl SFT reference: https://docs.axolotl.ai/docs/agents/sft.html

## Experiment and artifact contract

Every training run must record:

- Base model repository, immutable revision/commit, model license, tokenizer revision, and chat-template hash.
- Dataset manifest, source licenses/consents, preprocessing version, train/validation/test split identifiers, and dataset hashes.
- Framework version, dependency lock, training config, random seed, GPU type, precision, quantization, LoRA rank/alpha/target modules, and training steps.
- Output artifact type (adapter, merged weights, or exported quantized model), checksums, size, and exact inference compatibility.
- Baseline and candidate evaluation results, known regressions, reviewer, and promotion decision.

Keep checkpoints, datasets, logs, and credentials out of Git. Publish or transfer artifacts only after license, privacy, security, and integrity checks. Pin downloads by immutable revision and verify checksums. Never load an untrusted model artifact in the main desktop process.

## Promotion gate

A fine-tuned candidate is not automatically the new default. It must:

1. Beat the prompt-only baseline on the frozen task evaluation set.
2. Improve the targeted failure category without unacceptable regressions in general helpfulness, factuality, refusal behavior, tool selection, latency, memory, and cost.
3. Pass adversarial prompt-injection, permission-boundary, data-exfiltration, and tool-authorization tests.
4. Be tested with the same system prompt and inference template intended for production.
5. Run in a versioned, isolated candidate slot with a rollback path to the previous model.
6. Be explicitly approved before default activation.

Model weights never grant capabilities. The runtime's authorization checks, tool allow-lists, approval gates, and audit journal remain authoritative regardless of training.

## First Yuki experiment

Do not start with a broad "Yuki personality" fine-tune. First build a small, consented, versioned dataset around one measurable behavior (for example, concise task handoffs or structured research summaries). Compare:

- Baseline model + current system prompt.
- Same model + curated few-shot examples.
- Same model + retrieval/tool improvements, if applicable.
- LoRA/QLoRA candidate only if the preceding variants still fail the evaluation target.

If prompting reaches the acceptance threshold, stop: no training run and no new weights are needed. Record that result as a successful no-training decision.
