# Native Intelligence Architecture

## Decision

Yuki is the product and owns its application intelligence. External model providers are optional adapters, not the system of record, security boundary, task authority, or required foundation for local product workflows.

This is an architectural direction, not a claim that Yuki already contains a trained foundation model or human-level autonomous reasoning engine.

## Day-one intelligence without training from scratch

Yuki does not need a project-specific foundation-model training run before its first useful release. The product should ship with deterministic native capabilities—intent routing, planning, retrieval, validation, memory and guarded tool use—that work immediately and require no model service.

For broad natural-language generation and open-ended reasoning, there are only two honest options:
- Run pre-trained model weights locally. This avoids a hosted AI dependency and avoids training Yuki from scratch, but the weights are still a pre-trained model asset that must be selected, licensed, distributed or downloaded, and hardware-qualified.
- Train or distill a Yuki foundation model. This creates model weights owned by Yuki, but it is a separate research, data-governance, compute and evaluation programme; it cannot be promised as an effortless no-training feature.

The initial product path is local-first and model-optional: native deterministic intelligence remains available without weights, and an explicitly installed local model upgrades language capability without handing control to a third-party service. No cloud provider is required for startup, memory, planning, task state, permissions or execution.

The native router must abstain when capability matches are ambiguous. It may suggest registered capabilities, but the normal approval and guarded execution boundary remains mandatory.

## Ownership boundaries

- **Yuki core:** identity, conversation persistence, memory, deterministic planning, capability discovery, permissions, approvals, task state, audit, recovery, and user-facing workflows.
- **Native intelligence:** deterministic intent handling, policy-aware routing, retrieval over user-approved local material, structured planning, state machines, validation, and evidence-based synthesis. These capabilities must remain useful without a remote model.
- **Optional model adapters:** OpenAI, Gemini, Ollama, and future backends may provide language generation or model-assisted decomposition. They must implement narrow contracts and may not own permissions, persistence, or side-effect execution.
- **Execution boundary:** model output is untrusted input. Only Yuki's guarded tool/runtime layer may authorize and execute registered actions.

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
