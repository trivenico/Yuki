# Yuki continuous evolution architecture

## Product objective

Yuki should improve during normal use without requiring a GPU fine-tuning job for every correction. The default learning loop is application-level adaptation: approved memory, retrieval, few-shot examples, prompt/template updates, and reviewed capability upgrades. Model-weight training is an optional, periodic optimization when evaluation proves it is worthwhile.

This design does not claim that a frozen model changes its weights merely because it is being used.

## Four learning lanes

### 1. Personal memory (fast, low compute)

Capture only information the user explicitly asks Yuki to remember or has opted into remembering. Store typed, scoped records such as preference, project context, decision, recurring workflow, and temporary task context.

Each record should carry:
- stable ID, type, content, created/updated timestamps;
- source conversation/task reference kept in protected local metadata;
- consent/visibility state and retention/expiry policy;
- confidence and provenance;
- user-editable, exportable, and deletable lifecycle.

Retrieve only relevant records for a task, within a strict token/character budget. Memory is context, not policy: it must never override system/developer instructions, security controls, or the current user request. Sensitive categories require separate handling and must not be inferred into memory.

### 2. Behavioral adaptation (fast, low compute)

Turn explicit user corrections into candidate examples. A correction is not automatically training consent. The user must separately approve retaining an example for personalization and approve its use in an SFT dataset.

The runtime may retrieve a small number of approved, task-matched examples using the few-shot patch layer. Examples are untrusted reference data, not instructions. Keep a bounded prompt budget, provenance, expiry, deduplication, and a way to disable or remove each example.

### 3. Capability evolution (moderate compute)

Yuki can improve by adding reviewed skills, connectors, workflow templates, specialist handlers, and deterministic tools. Every capability upgrade must have:
- declared inputs/outputs and data classification;
- explicit capability allow-list and least-privilege authorization;
- resource/time limits and cancellation;
- tests, security review, audit events, and version pinning;
- staged rollout, health checks, and rollback.

A new skill is not allowed to silently grant itself permissions. Self-modification is a propose -> isolated branch -> test -> security review -> human approval -> staged rollout workflow.

### 4. Model adaptation (optional, scheduled)

The SFT generator curates only explicitly consented and human-approved examples. Dataset snapshots are immutable and versioned. Training runs in an isolated Kaggle or managed GPU environment, not inside the desktop assistant.

Use prompt/retrieval/tool changes first. Fine-tune only when a frozen evaluation set shows a repeatable gap that those cheaper mechanisms cannot close. Candidate adapters/weights must pass capability, regression, privacy, and safety evaluations before a human-approved promotion. Keep the previous model for rollback.

## Continuous learning loop

1. Observe task outcome and explicit user feedback.
2. Classify the signal: temporary context, durable memory, correction/example, missing capability, or model limitation.
3. Ask for consent when retaining personal information or creating a training example.
4. Validate, redact, deduplicate, and store the approved item with provenance.
5. Apply the cheapest suitable mechanism: memory retrieval, few-shot example, prompt/template patch, or reviewed skill.
6. Evaluate the next relevant tasks against a baseline.
7. Promote only if measured outcomes improve without safety or regression failures.
8. Periodically assess whether a model-training experiment is justified.

## Isolation and resource policy

- The interactive assistant always has priority over background curation and evaluation.
- No training, model download, or GPU job starts automatically during a conversation.
- Background work uses bounded queues, CPU/memory/time budgets, cancellation, and backpressure.
- SFT export and training datasets are separate from personal memory stores.
- No raw audio, voice samples, secrets, or unredacted private conversations enter training by default.
- Never send personal data to a remote trainer/provider without a separate, explicit destination-specific confirmation.
- Runtime learning never changes security policy, permissions, executable code, or active model weights.
- Keep audit trails for memory changes, example approval, capability upgrades, dataset snapshots, evaluations, and model promotions.

## Success metrics

Measure improvements rather than claiming that Yuki is learning:
- task completion and user correction rate by task type;
- retrieval relevance and memory usefulness;
- few-shot example acceptance and regression rate;
- latency and memory/CPU cost of retrieval;
- skill success, failure, and rollback rate;
- benchmark quality and safety deltas for any candidate model.

## Implementation status

This document is the target architecture. PR #1049 now includes an approval-gated `ContinuousLearningCoordinator` that composes the existing consent-based memory facade with staged correction review, separate training consent, injected durable-consent verification, authenticated reviewer verification, few-shot patch generation, and deterministic SFT export. An optional `EncryptedLearningStore` persists pending and approved records in an AES-256-GCM envelope, with its key held in a profile-scoped OS keyring account. Mutations use atomic replacement and cross-process file locking; data is revalidated after decryption, and missing keys or authentication failures fail closed. POSIX file and directory ownership/permissions are checked, and symlink/hard-link data-file paths are rejected. Windows currently relies on the private profile directory's inherited ACL; explicit native ACL verification remains a release gate. The coordinator rechecks consent and reviewer approval before rehydrating examples. Learning consent gates few-shot personalization; separate training consent gates SFT export and is required, alongside learning consent, to approve a new training example. The shared desktop conversation and composition roots now accept an optional `DesktopLearningSession`. The authenticated facade exposes an explicit selected-turn submission method and closes the session on logout; failed composition also closes it. `LearningCenterPanel` provides separate consent controls, a manual correction-entry dialog for an exact user message and corrected assistant answer, bounded correction previews, explicit approve/reject, and confirmed deletion. It never imports conversation history automatically. Storage, consent, audit, review, approval, rejection, submission, and deletion callbacks run through a single-operation Qt worker and return to the GUI thread. Delete-all requires a positive durable acknowledgement for each approval revocation before clearing sample content; audit failure aborts deletion. Production startup does not yet create/inject the session or add the panel to desktop tabs. No conversation history is scanned automatically, no training job starts, and no model weights are changed. Windows native ACL verification and end-to-end runtime evaluation remain release gates.
