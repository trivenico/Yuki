# Audit 083 — AI Provider Architecture, Model Routing, Failover, and Inference Reliability

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Scope:** provider factory, hybrid routing, capability registry, runtime provider selection, provider-agent adapter, OpenAI-compatible/Ollama streaming, Gemini CLI, and provider tests/docs.

## Executive summary

Yuki has a meaningful provider safety foundation: provider selection is explicit; the hybrid provider defaults to local execution; cloud task routing requires an allow-list; capability routing excludes cloud unless opted in; provider selection is constructed before active state is changed; provider adapters validate request/response envelopes; and streaming cleanup/idle-timeout limitations are documented.

The architecture is not yet a complete high-growth inference control plane. The reviewed capability metadata describes adapter-level features rather than individual model limits, provider readiness has provider-specific semantics, runtime selection is process-wide, and the agent adapter exposes only a narrow common chat contract. Cost, token/context limits, rate-limit state, health history, and request-level routing constraints are not represented as one end-to-end decision.

## Findings

### P1 — Provider readiness has inconsistent semantics

The OpenAI-compatible provider declares `availability_scope = "api_key_present_only"`; its `is_available()` checks only whether the configured API key is non-empty. Ollama declares `server_and_model_verified` and checks the local server and installed model. Gemini CLI's availability check verifies executable presence, while its own docstring states that account authentication is not checked.

The provider catalog/capability registry can therefore describe providers as ready based on materially different evidence. A ready cloud adapter may still fail at request time due to invalid credentials, account restrictions, model access, network, quota, or provider outage.

**Recommendation:** expose structured readiness levels (configured, executable/server reachable, authenticated, model accessible, request healthy) and preserve the evidence timestamp. Do not collapse them into one unqualified boolean. Routing should state which readiness level it requires and treat runtime failures separately.

### P1 — Provider routing lacks a unified model-level capability and budget contract

`providers/capabilities.py` explicitly describes known adapter-level capabilities, not undocumented capabilities of individual remote models. The inspected capability set does not model per-model context window, tokenizer, maximum output, supported modalities, tool-call compatibility, latency class, rate-limit state, or cost.

As a result, capability routing can select an adapter that supports a broad feature (such as text) without proving that its configured model supports the requested task's complete requirements or budget.

**Recommendation:** add versioned model descriptors sourced from verified provider metadata or explicit operator configuration. Include context/input/output limits, tokenizer/estimator, tool schema support, modalities, privacy/data residency, price dimensions, and evidence freshness. Route against a request contract, not provider name alone.

### P1 — Runtime provider selection is process-wide, not request-scoped

`ProviderRuntimeManager` documents that selection is process-wide and administrator-controlled. It mutates a shared `adapter.provider` and the shared config when selection changes. This is coherent for a single-runtime preference, but it cannot independently honor concurrent users/tasks that require different models, privacy policies, or latency/cost classes.

**Recommendation:** preserve the admin default, but introduce immutable request-scoped provider handles resolved from authenticated policy context. Pin the selected provider/model for the lifetime of a generation so a concurrent settings change cannot alter the intended execution path. Never accept arbitrary provider names or cloud opt-in directly from an untrusted user request.

### P2 — No automatic failover is a good safety default, but there is no policy-aware recovery layer

`ProviderManager.create_routed_adapter` explicitly states that a selected provider failure is terminal and no fallback is attempted. `HybridProvider` also refuses implicit cloud fallback when local execution is unavailable. This protects privacy and avoids silently changing provider behavior, but it leaves transient availability and retry policy to callers.

**Recommendation:** add an explicit orchestration policy with a bounded retry budget, error classification, circuit breaker, and opt-in fallback set. A fallback must satisfy the same privacy, tenant, model capability, tool-call, and cost constraints. Do not retry non-idempotent operations or tool execution as if they were ordinary inference.

### P2 — Streaming duration is not a hard wall-clock deadline

The provider streaming reliability document correctly states that synchronous `urllib` socket timeouts are idle/read timeouts, not total response deadlines. The agent loop checks cancellation and elapsed duration between received events, but cannot interrupt a currently blocked synchronous read. A peer that continuously sends data before each idle timeout can keep a stream open beyond the nominal execution budget.

**Recommendation:** retain the documented limitation until an interruptible transport or isolated I/O worker with bounded lifecycle is implemented. Add end-to-end deadline propagation and tests that distinguish idle timeout, total deadline, and caller cancellation. Do not add unmanaged background threads as a workaround.

### P2 — Provider adapters duplicate validation and do not expose a fully uniform operational contract

The OpenAI-compatible and Ollama adapters independently validate message roles/counts, tool definitions, and tool-call envelopes. `ProviderAgentAdapter` performs another layer of common validation. This is defense in depth, but duplicated logic can drift as providers evolve. The common adapter interface also does not standardize usage tokens, request IDs, finish reasons, provider error classes, rate-limit metadata, or cost estimates across all providers.

**Recommendation:** define a versioned provider protocol and normalized response/usage/error envelope. Keep provider-specific validation at adapter boundaries, backed by shared conformance tests. Emit sanitized operational metadata without logging prompts, credentials, or private context.

### P2 — Provider selection persistence is atomic locally but not a distributed control plane

`ProviderRuntimeManager` persists only a provider identifier using a private temporary file, fsync, and atomic replace. This is appropriate for one host process. It does not provide distributed configuration propagation, audit history, rollback, or coordinated draining of in-flight generations across multiple workers.

**Recommendation:** if Yuki runs multiple workers, use a versioned centralized configuration/control plane with authenticated admin changes, audit events, rollout/rollback, and per-generation provider pinning. Keep secrets outside the selection record.

## Existing positive controls

- Hybrid routing is local-first and requires explicit cloud task allow-listing.
- Missing/unavailable local provider does not silently route to cloud.
- Capability routing requires explicit cloud opt-in and rejects unknown privacy classifications.
- Routed adapter creation checks availability and fails closed without fallback.
- Runtime provider selection constructs the candidate before changing active selection.
- Provider selection state stores a provider identifier, not credentials, and is atomically persisted with restrictive file permissions.
- OpenAI-compatible and Ollama adapters validate message/tool schemas and normalize tool proposals.
- Provider-agent integration routes tool proposals through the bounded agent loop and guarded tool protocol.
- Provider streaming reliability documentation correctly distinguishes idle timeout from hard wall-clock cancellation.

## Recommended Yuki inference control plane

1. **Request policy:** derive privacy, tenant, task, tool, cost, and latency constraints from trusted server-side context.
2. **Model registry:** versioned per-model capabilities and evidence freshness.
3. **Readiness and health:** separate configuration, authentication, model access, live health, quota, and circuit-breaker state.
4. **Deterministic router:** rank only providers that satisfy every hard constraint; make cloud routing explicit.
5. **Generation pinning:** bind each generation to one provider/model/config revision.
6. **Recovery policy:** bounded transient retries and opt-in privacy-preserving failover; never replay tool side effects.
7. **Streaming lifecycle:** propagate deadlines/cancellation to interruptible transports and guarantee response cleanup.
8. **Normalized telemetry:** provider/model IDs, latency, usage, finish reason, sanitized error class, cost estimate, and routing decision.
9. **Conformance suite:** run the same contract tests against every provider adapter and streaming mode.

## Required regression/adversarial tests

- API key present but invalid credentials is not reported as authenticated/healthy.
- Gemini executable present but account unauthenticated is represented distinctly.
- Routing rejects a model that cannot satisfy context, tool, modality, privacy, or cost constraints.
- Cloud fallback cannot occur unless explicitly allowed by trusted policy.
- Fallback cannot weaken tenant isolation, privacy, or tool authorization.
- Provider selection changes do not alter an already-started generation's pinned adapter.
- Transient retry is bounded and never repeats tool dispatch or non-idempotent side effects.
- Streaming tests distinguish idle timeout, total deadline, and cancellation while blocked.
- Every provider passes shared message, tool, response, error, and streaming conformance tests.
- Multi-worker provider config changes are auditable and roll back safely.

## Validation limits

Source inspection only. No runtime code was changed and no tests were executed. No real provider credentials, network requests, rate-limit simulation, cost benchmark, multi-worker deployment, streaming slow-peer test, or provider failover chaos test was performed. Readiness semantics are described from the inspected adapter implementations and should not be generalized to every deployment integration.
