# Native Intelligence Architecture

## Decision

Yuki is the product and owns its application intelligence. External model providers are optional adapters, not the system of record, security boundary, task authority, or required foundation for local product workflows.

This is an architectural direction, not a claim that Yuki already contains a trained foundation model or human-level autonomous reasoning engine.

## Ownership boundaries

- **Yuki core:** identity, conversation persistence, memory, deterministic planning, capability discovery, permissions, approvals, task state, audit, recovery, and user-facing workflows.
- **Native intelligence:** deterministic intent handling, policy-aware routing, retrieval over user-approved local material, structured planning, state machines, validation, and evidence-based synthesis. These capabilities must remain useful without a remote model.
- **Optional model adapters:** OpenAI, Gemini, Ollama, and future backends may provide language generation or model-assisted decomposition. They must implement narrow contracts and may not own permissions, persistence, or side-effect execution.
- **Execution boundary:** model output is untrusted input. Only Yuki's guarded tool/runtime layer may authorize and execute registered actions.

## Current implementation boundary

The provider factory now selects Yuki's deterministic native-core adapter by default. It performs no network requests, does not call a remote model, and always returns an explicit no-tool-call response. The installed CLI, desktop and API compositions can therefore construct their common agent loop without external credentials. Selecting OpenAI, Gemini, Ollama or hybrid remains an explicit configuration/setup choice.

This adapter is a startup and deterministic-response foundation, not a trained model or general natural-language reasoning engine. Native intent routing, richer task/memory commands, retrieval and local model training remain separate implementation work.

## Degraded operation

Yuki must start and expose its local/core workflows when no model is configured, credentials are absent, or a provider is unavailable. Model-dependent free-form generation may be reported as unavailable, but local commands, memory, task inspection, deterministic planning, approvals, and other independently implemented core features must not be disabled as a side effect.

No implicit cloud fallback is permitted. Provider selection and cloud routing remain explicit. A provider's configured/installed status must not be described as verified inference readiness unless the relevant check has actually run.

## Build sequence

1. Stabilize provider-independent application services and make startup independent of external model credentials.
2. Define and test deterministic native intent, planning, retrieval, and workflow contracts.
3. Keep model adapters behind the same narrow interfaces; test core workflows with providers absent.
4. Add optional local inference and optional remote adapters as interchangeable enhancements.
5. Evaluate native capabilities against fixed, versioned task suites before describing them as intelligent, autonomous, or production-ready.
6. Keep every side effect behind existing authorization, approval, audit, and recovery controls regardless of which model proposed it.

## Non-goals and claims discipline

- This decision does not claim that Yuki has trained its own foundation model.
- A deterministic planner or rules engine is not a substitute for general language understanding.
- A provider adapter is not an agent and cannot grant itself capabilities.
- A catalog entry or specialist profile is not a running model instance.
- Model unavailability must not be hidden by returning fabricated or mock-generated answers in production.

## Acceptance criteria

- A clean installation can launch Yuki's core interface without an API key or model download.
- Provider-independent commands and local workflows pass tests with all model adapters absent.
- Missing-provider behavior is explicit, safe, and does not silently substitute a test mock.
- Optional adapters can be enabled, selected, tested, and removed without changing core data formats or permission policy.
- Provider failures do not corrupt conversation/task state or bypass cancellation, audit, or recovery.
