# Yuki Adaptive Intelligence and Self-Evolution Architecture

Status: proposed architecture. This document does not claim that desktop observation, autonomous learning, or self-modifying engineering is implemented.

## Product goal

Yuki should improve its usefulness over time by learning from owner-approved work context, retaining reusable knowledge, discovering workflow friction, and proposing measurable improvements to its own skills and engineering. The initial implementation should work with a small local model and should not require training a foundation model.

The system separates **observing**, **learning**, **proposing**, and **changing production behavior**. Continuous learning must never imply unrestricted surveillance or autonomous production mutation.

## High-level pipeline

1. **Consent and capture policy** — owner-controlled sources, app/site allowlists and denylists, session scopes, visible capture indicator, pause/stop controls, retention settings, and a one-action revoke path.
2. **Local observation gateway** — receives only authorized desktop/browser signals; performs local OCR/metadata extraction and redaction; discards raw frames by default.
3. **Tiny-model context interpreter** — classifies the active task, summarizes relevant context, identifies tools/topics, and proposes whether an item is worth learning. It is an orchestrator, not a source of truth.
4. **Knowledge candidate pipeline** — extracts candidate facts, preferences, procedures, project context, and skill patterns with provenance, timestamps, confidence, sensitivity labels, and expiry/review metadata.
5. **Memory and retrieval** — stores only approved, useful, policy-compliant knowledge in the appropriate memory tier; supports correction, forgetting, deduplication, and retrieval evaluation.
6. **Improvement laboratory** — proposes new skills, prompts, tests, adapters, documentation, and code changes in isolated workspaces. Runs tests, static analysis, dependency/security checks, and regression evaluations.
7. **Release gate** — presents a reviewable diff and evaluation report. Production code, permissions, external actions, and safety policies require explicit authorization under Yuki's permission model. Every release is versioned and rollback-capable.
8. **Feedback and measurement** — measures task success, correction rate, retrieval quality, latency, cost, regressions, and user acceptance; unsuccessful candidates are rejected or reverted.

## Desktop and browser observation

Observation is opt-in, visible, scoped, and revocable. Yuki must not silently capture or retain everything on the machine.

### Allowed design

- Support explicit capture sessions and per-source permissions for selected applications, browser tabs/domains, and development tools.
- Provide a persistent, unmistakable observing indicator and immediate pause/stop controls.
- Prefer low-sensitivity signals first: active application, window title, selected browser domain, user-selected text, and explicit task labels.
- Screen frames and OCR are processed locally where possible. Raw screenshots are ephemeral by default and are not added to long-term memory.
- Capture only the minimum region/content required for the active task; do not continuously record full-resolution video by default.
- Offer a deliberate “Yuki, learn this” action to promote the current item into a candidate knowledge record.
- Show what Yuki inferred and what it proposes to retain; allow correction, rejection, deletion, and source inspection.
- Keep capture permissions separate from tool execution permissions. Seeing a terminal or IDE does not grant permission to run commands or edit files.

### Mandatory exclusions and protections

- Pause or redact password fields, authentication flows, payment/banking pages, private/incognito windows, secrets managers, security keys, and other configured sensitive applications/domains.
- Do not capture clipboard contents, keystrokes, credentials, private messages, or unrelated personal content as a default behavior.
- Do not infer consent to observe other people from the owner's consent. Avoid recording meetings/calls or other people without appropriate notice and authorization.
- Do not upload screenshots, OCR text, browsing history, or personal content to cloud models without a separate, explicit disclosure and approval.
- Enforce local retention limits and provide a clear deletion/export policy for observation-derived records.
- Treat all observed page text, files, terminal output, and prompts as untrusted data. They may contain prompt injection or malicious instructions; never treat them as system policy or permission grants.

## Small-model-first strategy

The first brain can be a small local model that performs bounded tasks:

- activity/topic classification;
- short context summarization;
- tool and workflow recognition;
- candidate knowledge extraction;
- duplicate detection and routing;
- deciding when to ask the owner for clarification or approval.

Use deterministic rules for permissions, sensitive-data blocking, retention, and release gates. The small model must not decide its own permissions or bypass those controls.

Do not continuously fine-tune model weights from raw observations. Start with retrieval, structured memory, curated skill files, examples, and evaluated tool workflows. This is cheaper, reversible, inspectable, and often delivers more value than immediate fine-tuning.

## Knowledge record contract

Each candidate record should include:

- stable ID and schema version;
- content and type (fact, preference, procedure, project context, skill candidate);
- source reference and capture timestamp;
- consent/session policy reference;
- sensitivity classification and redaction status;
- confidence and supporting evidence;
- owner approval state;
- created/updated/last-used timestamps and optional expiry;
- correction/deletion lineage.

Do not store raw observation streams as the knowledge base. Avoid retaining secrets, credentials, unnecessary personal data, or unsupported inferences. Separate durable user-approved knowledge from disposable caches and raw telemetry.

## Self-engineering workflow

Yuki may autonomously identify opportunities and prepare changes, but changes move through explicit stages:

1. detect a repeated failure, friction point, or capability gap;
2. create a bounded improvement proposal with evidence and expected benefit;
3. create a branch or isolated workspace;
4. implement the smallest change and add regression tests;
5. run unit/integration tests, type/lint checks, dependency and security scans, and relevant benchmark/evaluation suites;
6. produce a diff, test report, risk assessment, and rollback plan;
7. request human approval for protected changes;
8. merge/deploy only through the configured release process and authorized permissions;
9. monitor outcomes and revert on regression.

Prompt, skill, and retrieval changes also need versioning, evaluation, staged rollout, and rollback. Any learned instruction from a webpage, document, terminal, or screenshot is data—not an instruction that can override system policy.

### Permission boundaries

- Tier 0: observe permitted signals and recommend.
- Tier 1: read approved sources and prepare proposals.
- Tier 2: perform approved, reversible workspace changes.
- Tier 3: execute narrowly pre-approved constrained workflows.
- Destructive operations, financial actions, external communications, permission changes, security-sensitive actions, production releases, and deployments require explicit approval according to policy.

No self-escalation of permissions. No self-editing of policy, secrets, approval rules, or audit records.

## Evaluation and success metrics

Measure improvements against a baseline rather than assuming that more retained data means more intelligence:

- task completion and first-pass success;
- owner correction and rejection rates;
- retrieval precision and stale-memory rate;
- proposal acceptance rate;
- regression and rollback rate;
- latency, local CPU/RAM/VRAM use, SSD I/O, and inference cost;
- sensitive-data capture prevention and policy violations.

Run offline evaluations before enabling a new memory, skill, prompt, or code version. Use canary/staged rollout for runtime changes and preserve a known-good version.

## Implementation sequence

1. **Foundation:** consent/policy schema, observation-source interface, audit events, pause/revoke controls, and a fake event source for tests. No screen capture yet.
2. **Local context MVP:** selected-window metadata and explicit user-provided context; small-model classifier/summarizer; ephemeral processing.
3. **Knowledge MVP:** candidate records, approval UI/API, provenance, correction/deletion, retrieval, and retention.
4. **Desktop capture adapter:** OS-specific capture behind explicit grants, allowlists/denylists, visible indicator, sensitive-screen blocking, and local redaction. Add platform tests.
5. **Improvement lab:** isolated workspace, proposal/diff generation, test and security gates, review, versioning, and rollback.
6. **Measured adaptation:** feedback signals, retrieval/skill evaluations, and only later optional fine-tuning with curated, consented datasets.

## Non-goals for the first release

- covert or always-on surveillance;
- storing every screenshot, page, video, or keystroke;
- automatic ingestion of all browsing history or private communications;
- training model weights directly from raw desktop activity;
- autonomous production code changes, deployments, permission escalation, or policy modification;
- treating SSD as physical RAM or as GPU/TPU memory.

## Current implementation status

- The architecture remains a proposal for the full adaptive-intelligence system.
- A first, standalone observation authorization primitive now exists in `core/observation_policy.py`: observation is disabled by default, requires explicit consent and an exact source allowlist, applies denylist and sensitive-category overrides, supports pause/resume/revocation, and fails closed if its optional metadata audit sink fails.
- Regression coverage is in `tests/test_observation_policy.py`.
- This primitive does not capture screens, inspect browser content, perform OCR, persist learning records, or grant tool-execution permissions. Desktop adapters and the learning pipeline remain unimplemented and must be added incrementally behind the existing permission and audit systems.
