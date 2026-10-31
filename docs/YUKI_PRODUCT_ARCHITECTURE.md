# Yuki product and architecture definition

## Product definition

Yuki is one product with four cooperating layers:

1. **Yuki AI — intelligence layer.** Provider/model adapters, prompt/context assembly, retrieval, evaluation, and bounded planning. Supports local inference and explicitly configured cloud inference.
2. **Yuki Companion — interaction layer.** Yuki's identity, conversational style, session continuity, consent-based preferences, memory controls, reminders, and optional voice experience.
3. **Yuki Agent — action layer.** Registered skills/tools, specialist roles, task planning, workflow execution, and supervised delegation. Model output is a proposal, never authority.
4. **Yuki Platform — delivery layer.** Python application services, CLI, future desktop client, storage, provider gateway, observability, packaging, and deployment.

These are architectural layers of the same Yuki application, not four separately deployed products.

## Core request flow

`User → Companion session → AI provider/model → bounded plan → policy/approval gate → registered capability → observed result → Companion response`

For ordinary conversation, the path ends after the model response. Tool execution is never implied by a chat response.

## Trust boundaries

- Retrieved documents, skill descriptions, conversation history, and model output are untrusted input.
- **Structured chat implementation in progress:** the provider contract now has a `generate_chat(system, messages)` method and strict validation for bounded, alternating user/assistant history. OpenAI-compatible adapters send a distinct provider-level `system` message; Ollama uses `/api/chat`; the direct Gemini API adapter uses Gemini `systemInstruction` and maps assistant history to the provider's `model` role. Gemini CLI remains a separate integration and does not implement structured chat, so companion chat fails closed for that provider. Hybrid mode keeps companion chat local and fails closed rather than flattening roles or silently routing to cloud. Legacy `generate(prompt)` remains for non-companion one-shot calls. This separation is an important defense-in-depth improvement, not a guarantee against prompt injection; deterministic policy and approval checks remain mandatory.
- Only capabilities registered by trusted application code are eligible for invocation.
- Role profiles (companion, tutor, interviewer, developer, security, marketing) change response guidance only. They do not grant tools, permissions, or access.
- Every action requiring approval must pass the existing PermissionGate/approval workflow. Denial, missing approval, provider failure, malformed model output, or exhausted budgets fail closed.
- Local inference is the default privacy route in hybrid mode. Cloud routing is explicit and allow-listed; there is no implicit cloud fallback when local inference fails.
- Companion chat history in the CLI conversation session is ephemeral and is cleared when the session exits or the user resets it. Persistent memory is a separate opt-in feature: it is off by default, session-scoped consent is required before `/remember`, existing entries are never silently erased by disabling memory, and `/forget`, `/memory export`, and `/memory clear confirm` provide user controls. Saved entries are not automatically injected into model context. The current JSON store is local plaintext (filesystem permissions only); do not store secrets or sensitive personal data until encryption-at-rest and key-management are implemented. The data file is git-ignored.

## Client strategy

CLI and desktop should be clients of the same application services. Do not duplicate provider routing, memory policy, permissions, task execution, or business logic in the desktop UI. The desktop app should call a stable service/API boundary shared with CLI.

## Specialist agent strategy

The 100-role catalog is a taxonomy and routing vocabulary, not 100 running autonomous agents. Roles become executable only when backed by a reviewed handler, explicit capability allow-list, bounded context/step/time/output budgets, authorization checks, audit events, and tests. Delegation must not elevate permissions.

## Production readiness definition

Yuki is not production-ready solely because unit tests pass or a capability exists in the repository. A release candidate must demonstrate:

- reproducible clean installation and upgrade on supported operating systems;
- provider behavior tested against real configured local and cloud endpoints, including timeout, cancellation, malformed response, and outage cases;
- end-to-end tests for approval, denial, revoked access, and unregistered tool requests;
- adversarial prompt-injection and untrusted-skill tests;
- memory export, explicit deletion, retention, backup, and restore behavior;
- secret scanning and credential rotation review, dependency/license review, and repository visibility decision;
- bounded logging/telemetry with redaction, health checks, graceful shutdown, and crash recovery;
- signed release artifacts and a documented rollback/update path;
- documented support scope and known limitations.

## Current implementation boundaries (October 2026)

- The repository contains Python provider adapters, a local/cloud hybrid router, guarded skill execution, a bounded model-guided task loop, durable task/workflow foundations, companion persona and local memory primitives, reminders, and role profiles.
- The companion conversation session and CLI integration are in draft PR #423; safe role selection is stacked in draft PR #424; structured provider chat, explicit CLI memory consent, and the direct Gemini API adapter are in draft PR #425. The memory store remains plaintext and is not yet suitable for secrets or sensitive personal data.
- The 100 specialist role catalog is metadata only in draft PR #422.
- Native desktop packaging, production cloud deployment, encrypted-at-rest memory, desktop memory-consent UX, retention scheduling, and full release qualification remain unfinished.
- Do not advertise these unfinished capabilities as production features.

## Deployment direction

Support both local and cloud model hosting. Start with local Ollama for privacy and cost control, use cloud models only for explicitly configured tasks, and keep OCI deployment optional and resource-budget-aware. The first cloud deployment should host the Yuki application/control plane; GPU model hosting is a separate capacity and cost decision, not assumed to fit Oracle Cloud Free Tier.
