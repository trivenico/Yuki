# Parallel Yuki usage and supervised fine-tuning pipeline

## Goal

See [Yuki continuous evolution architecture](YUKI_CONTINUOUS_EVOLUTION.md) for the broader low-compute learning strategy: approved memory, behavioral retrieval, reviewed capability growth, and optional model adaptation.


Keep the desktop assistant usable while a separate, opt-in pipeline prepares training data and candidate models. Training must never block or mutate the live inference runtime. Day-to-day improvement should primarily use low-compute application-level adaptation; weight training is optional and evidence-driven.

## Data flow

1. A user explicitly marks a specific interaction or curated correction as eligible for training.
2. A human reviewer approves the example. Consent and approval are separate mandatory checks.
3. The SFT data generator validates message structure, rejects likely credentials/private keys, deduplicates examples, hashes source references and approver identifiers, records the consent/approval state, and returns JSONL in memory.
4. The few-shot patch layer loads only human-approved JSONL rows, validates required consent/approval metadata and recomputes each example content digest before retrieving a small, bounded set at inference time. This detects accidental or unsophisticated tampering; SHA-256 metadata is not a cryptographic reviewer signature, so dataset provenance must still be trusted. Examples are marked as untrusted historical data, not instructions.
5. The template consistency checker validates a versioned template manifest, exact placeholder sets, and matching bundle/template versions. It returns a stable SHA-256 digest.
6. A separate training runner may consume a reviewed dataset snapshot on Kaggle or another isolated GPU environment. It produces a candidate adapter/checkpoint, never an automatically activated production model.
7. The candidate is evaluated against a frozen benchmark and safety regression suite. Promotion and rollback remain explicit operations.

## Important boundaries

- No conversation history is scanned or exported automatically.
- No data is sent to Kaggle, Hugging Face, Unsloth, Axolotl or any remote service by these modules.
- The SFT generator returns deterministic JSONL text; the optional encrypted learning store separately persists curated pending/approved records using AES-256-GCM and a profile-scoped OS keyring key. This is local persistence only, not remote export or upload.
- Both explicit training consent and human approval are required for each example. `ContinuousLearningCoordinator` fails closed unless the application injects verifiers backed by authenticated, durable consent and approval records; boolean arguments alone are not treated as authorization.
- The generator rejects obvious credential/private-key patterns. This is not a complete PII detector; reviewers must remove personal, confidential, regulated, and third-party data before approval.
- Source references and approver identifiers are hashed before export. Original references and approver names are not included in JSONL.
- Few-shot examples cannot override system/developer policy and are not treated as trusted instructions.
- Template checks are structural checks, not a substitute for prompt-injection, behavior, or safety evaluations.
- Training and inference must use separate processes, resource budgets, credentials, caches, and deployment permissions.
- A new adapter or weights file is promoted only after evaluation, security review, and explicit approval. Keep the previous model available for rollback.

## Current scope

This is a reusable foundation with an optional integration point in the shared desktop conversation adapter: when an application explicitly injects a configured learning coordinator, its approved few-shot patch is composed with the existing consent-gated memory context. The production desktop startup path does not yet construct or inject that coordinator, so few-shot learning is not active in the shipped desktop session. The encrypted store and desktop profile factory exist, and the coordinator can use the store when explicitly supplied. The local `LearningAuthorizationLedger` and desktop coordinator factory now provide actor-bound, durable consent and per-proposal approval verification using the existing hash-chained metadata-only audit log. The chain is tamper-evident but not signed non-repudiation. Learning Center/reviewer UI, production desktop startup/logout wiring, Windows native ACL verification, remote export/upload, Kaggle job, training, and model hot-swap remain subsequent milestones.
