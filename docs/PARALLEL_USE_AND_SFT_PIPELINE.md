# Parallel Yuki usage and supervised fine-tuning pipeline

## Goal

Keep the desktop assistant usable while a separate, opt-in pipeline prepares training data and candidate models. Training must never block or mutate the live inference runtime.

## Data flow

1. A user explicitly marks a specific interaction or curated correction as eligible for training.
2. A human reviewer approves the example. Consent and approval are separate mandatory checks.
3. The SFT data generator validates message structure, rejects likely credentials/private keys, deduplicates examples, hashes source references and approver identifiers, records the consent/approval state, and returns JSONL in memory.
4. The few-shot patch layer loads only human-approved JSONL rows and retrieves a small, bounded set at inference time. Examples are marked as untrusted historical data, not instructions.
5. The template consistency checker validates a versioned template manifest, exact placeholder sets, and matching bundle/template versions. It returns a stable SHA-256 digest.
6. A separate training runner may consume a reviewed dataset snapshot on Kaggle or another isolated GPU environment. It produces a candidate adapter/checkpoint, never an automatically activated production model.
7. The candidate is evaluated against a frozen benchmark and safety regression suite. Promotion and rollback remain explicit operations.

## Important boundaries

- No conversation history is scanned or exported automatically.
- No data is sent to Kaggle, Hugging Face, Unsloth, Axolotl or any remote service by these modules.
- The generator returns JSONL text in memory; persistence, encryption, export, and upload require separate explicit workflows.
- Both explicit training consent and human approval are required for each example.
- The generator rejects obvious credential/private-key patterns. This is not a complete PII detector; reviewers must remove personal, confidential, regulated, and third-party data before approval.
- Source references and approver identifiers are hashed before export. Original references and approver names are not included in JSONL.
- Few-shot examples cannot override system/developer policy and are not treated as trusted instructions.
- Template checks are structural checks, not a substitute for prompt-injection, behavior, or safety evaluations.
- Training and inference must use separate processes, resource budgets, credentials, caches, and deployment permissions.
- A new adapter or weights file is promoted only after evaluation, security review, and explicit approval. Keep the previous model available for rollback.

## Current scope

This is a reusable foundation. It does not yet connect to the desktop conversation UI, provide a reviewer dashboard, upload data, launch Kaggle jobs, train weights, or hot-swap models. Those integrations are subsequent milestones.
