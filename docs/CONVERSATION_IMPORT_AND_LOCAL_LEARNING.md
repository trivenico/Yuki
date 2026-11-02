# Conversation import and local learning

## Supported initial import

Yuki previews and imports user-supplied UTF-8 JSON exports in common shapes: ChatGPT-style mapping exports, generic messages arrays, top-level message lists, and conversations arrays containing supported messages. This is not universal compatibility with every Claude, DeepSeek, Kimi, Minimax or GLM export. Each provider needs fixture-based parser tests; HTML, ZIP, attachments, image/audio content and tool traces require dedicated parsers.

## Consent-based lifecycle

1. User selects an export file.
2. Yuki previews message count, roles and detected credential redactions.
3. User explicitly confirms local import.
4. Yuki normalizes messages, redacts common credential patterns, deduplicates imports and stores locally.
5. Imported content remains untrusted evidence, not instructions or approved training examples.
6. Local model analysis is a separate opt-in action. Users can delete imported records.

## Three distinct learning levels

- Retrieval/memory: approved lessons and workflows are available as context. Model weights do not change.
- Local inference analysis: Ollama can summarize patterns, corrections and candidate lessons. This is inference, not training.
- Fine-tuning: a human-reviewed sanitized dataset can feed a separate offline PEFT/LoRA training job. The resulting adapter must be evaluated, versioned, reversible and explicitly promoted.

Ollama is primarily an inference/runtime manager; it does not itself provide a general model-weight training pipeline.

## Privacy, rights and compute

Only import conversations the user is authorized to use. Respect provider terms, copyright, privacy commitments and other participants' expectations. Redact credentials and personal data; do not train on secrets or raw dumps by default. Avoid inferring sensitive personal traits.

Local models avoid per-token API billing and external provider quotas, but are not unlimited: they consume RAM/VRAM, storage, electricity and compute time. Background jobs must be queued, resource-limited, pausable and visible, and must not starve interactive use.

## Next steps

Add provider-specific export fixtures, desktop preview/consent UI, local Ollama job queue, stronger redaction and retention controls, reviewed lesson promotion, and a separately sandboxed LoRA training/evaluation worker. Keep model promotion manual until benchmark gates pass.
