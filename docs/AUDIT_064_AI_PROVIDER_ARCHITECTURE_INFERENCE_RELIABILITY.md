# Yuki Engineering Audit 064 — AI Provider Architecture, Model Routing, and Inference Reliability

**Review date:** 2026-11-23  
**Repository:** `trivenico/Yuki`  
**Review type:** Targeted source and test inspection  
**Status:** Documentation audit; no tests, live provider calls, or load tests executed.

## Executive assessment

Yuki has a useful provider foundation with explicit adapters for OpenAI-compatible APIs, Ollama, Gemini CLI, and a hybrid text router. The design includes meaningful security controls: trusted HTTPS cloud hosts, redirect rejection, environment-only cloud credentials, bounded transcripts and response sizes, tool proposal validation, local-first hybrid routing, and a constrained Gemini subprocess environment.

The main production risks are not a missing provider abstraction. They are lifecycle and reliability gaps around provider switching, end-to-end deadlines, cancellation, retry semantics, usage accounting, and uniform capability contracts.

Key conclusions:
- Provider adapters have different capability surfaces; a single `BaseProvider` contract is too small to express chat, structured tools, streaming, cancellation, health, and lifecycle.
- HTTP timeout arguments generally bound socket operations, not necessarily the complete end-to-end inference deadline. Streaming can continue as long as data keeps arriving.
- Provider switching is process-wide and synchronized inside `ProviderRuntimeManager`, but in-flight calls and old provider disposal are not coordinated by that manager.
- Hybrid routing is intentionally explicit and local-first, but its text-only contract cannot transparently serve structured agent chat.
- Token usage tracking is a bounded local JSON log, not a durable multi-process billing ledger or price-aware budget enforcement system.

## 1. Provider interface and capability model

### Inspected
- `providers/base.py`
- `providers/capabilities.py`
- `providers/agent_adapter.py`
- `providers/factory.py`
- `providers/catalog.py`
- `providers/runtime_manager.py`

Observed:
- `BaseProvider` defines only `generate(prompt)` and `is_available()`.
- Structured chat and streaming are optional adapter methods, discovered dynamically by `ProviderAgentAdapter`.
- Provider catalog reports implemented/readiness/privacy metadata.
- Factory rejects unknown providers and does not introduce implicit cloud fallback.
- Runtime selection is process-wide and administrator-controlled; persisted selection contains only a provider identifier, not credentials.

Recommendations:
1. Define versioned capability contracts: text generation, chat roles, structured tool proposals, streaming, usage metadata, cancellation, health, and close.
2. Make capability negotiation explicit and fail before accepting a request if the selected model cannot satisfy its requested contract.
3. Keep provider-specific response normalization in adapters; validate the normalized envelope again at the common boundary.
4. Publish model context limits, output limits, supported modalities, tool-call limits, and streaming support as verified capability metadata.
5. Avoid treating `is_available()` as proof of successful authentication or inference. Readiness must state exactly what was checked.

## 2. Cloud endpoint trust and credentials

### `providers/openai_compatible.py`

Observed:
- Cloud endpoint must use HTTPS and an exact allow-listed hostname.
- Credentials in endpoint URLs, query strings, and fragments are rejected.
- Redirects are rejected to prevent forwarding prompts and bearer credentials to another host.
- API keys are read from the configured environment variable.
- Transcript size, tool count, tool schema size, response bytes, and tool-call count are bounded.
- Tool proposals are checked against the advertised tool names and normalized before returning to the agent loop.

Strengths:
- The endpoint trust boundary is explicit and avoids arbitrary URL routing.
- Redirect rejection is a good defense against credential/prompt leakage.
- Provider tool proposals remain proposals; the guarded execution boundary remains elsewhere in Yuki.

Follow-up risks:
- Exact hostname allow-listing does not alone establish DNS/IP pinning or prevent DNS rebinding between validation and connection. Deployment DNS and egress policy remain part of the trust boundary.
- Custom allowed-host configuration and `YUKI_CLOUD_ALLOWED_HOSTS` should be treated as privileged configuration and audited.
- Ensure provider error bodies, request headers, prompts, and tool arguments never enter logs or exception telemetry.
- Define TLS trust, proxy behavior, and egress restrictions consistently across all cloud adapters.

## 3. Local Ollama endpoint

### `providers/ollama.py`

Observed:
- Defaults to loopback HTTP; remote endpoints require HTTPS.
- Rejects URL credentials, query, and fragment.
- Rejects redirects.
- Checks installed model availability.
- Bounds transcript, tool schema, response body, and streamed response bytes.
- Normalizes tool proposals and rejects names not in the advertised tool set.

Recommendations:
- Treat local inference as a privacy boundary, not an automatic trust boundary. A local daemon may be shared, compromised, or configured to proxy remotely.
- Make the remote Ollama trust policy explicit; consider exact host allow-lists or deployment egress controls for non-loopback endpoints.
- Verify that model identity/version is stable across a request and record the resolved model digest when the server exposes it.
- Add provider health checks that do not repeatedly trigger expensive model loads.
- Add tests for malformed NDJSON, missing terminal event, stream disconnect, very slow token trickle, and oversized individual lines.

## 4. Gemini CLI process isolation

### `providers/gemini_cli.py`

Observed:
- Invokes only the official `gemini` executable; no shell invocation.
- Uses a minimal child-process environment allow-list.
- Uses a temporary working directory, sandbox and plan approval arguments.
- Discards stderr, drains stdout continuously, retains only bounded output, and uses a deadline.
- Starts a process group on POSIX and kills/reaps the child on timeout or interruption.
- Uses bounded input/output worker threads and sanitized errors.

Strengths:
- This is materially stronger process hygiene than a generic subprocess wrapper.
- Prompt text is passed over stdin rather than command-line arguments.
- The adapter distinguishes executable presence from verified authentication.

Recommendations:
- Confirm the CLI's sandbox semantics and version compatibility in supported deployment images; arguments alone are not proof of a particular isolation level.
- On Windows, verify child-process tree termination (not only direct child termination) and handle descendants.
- Add an explicit maximum total process lifetime and a bounded process/thread concurrency gate.
- Test repeated timeouts and interruptions for orphan processes, temporary-directory cleanup, and lingering pipe threads.
- Do not advertise CLI availability as authenticated readiness; preserve the current distinction.

## 5. Streaming and end-to-end deadlines

### Inspected
- `providers/openai_compatible.py`: `generate_chat` and `generate_chat_stream`
- `providers/ollama.py`: chat and newline-delimited JSON streaming
- `providers/agent_adapter.py`: `stream_model`
- `core/agent_execution_loop.py`

Observed:
- Cloud and Ollama streaming parsers bound line size and aggregate response bytes and normalize text/tool events.
- Provider adapters pass a configured timeout to the underlying URL opener.
- The agent execution loop has a cooperative duration budget and cancellation checks between model turns and before tool dispatch.

Critical limitation:
- A socket timeout is not a complete wall-clock deadline for the whole inference operation. With streaming responses, a peer that sends data periodically can keep a request alive longer than the intended overall budget.
- The synchronous agent loop cannot interrupt a blocking provider read while it is in progress. Cancellation is observed only when control returns to the loop.
- The streaming adapter returns the provider iterator directly; cancellation/close behavior depends on the consumer closing the iterator and the underlying response context unwinding.

Required:
1. Introduce a monotonic absolute deadline propagated from request/task to provider adapter and stream parser.
2. Apply both connect/read inactivity limits and total operation deadline.
3. Ensure cancellation closes the active response/socket and stops any provider subprocess.
4. Add a concurrency semaphore per provider/model and a bounded waiting queue with rejection/backpressure.
5. Enforce maximum generated tokens/output bytes, not only input transcript size and response transport bytes.
6. Define partial-stream semantics: whether partial text is returned, discarded, or stored as an interrupted result.
7. Test slow-trickle responses, cancellation during read, consumer abandoning iteration, disconnect mid-event, and deadline expiry.

## 6. Retry, fallback, and circuit breaking

Observed:
- Factory and hybrid routing intentionally avoid implicit fallback.
- Hybrid routes general tasks to local and only explicitly named tasks to cloud.
- If the selected local provider is unavailable, hybrid fails rather than silently sending private data to cloud.
- The inspected provider adapters do not implement a common retry/circuit-breaker policy.

Recommendations:
- Preserve no-implicit-cloud-fallback as a privacy invariant.
- Define retries at a single orchestration layer, not independently in each adapter.
- Retry only classified transient failures (connection reset, selected 429/5xx responses) and respect provider retry-after metadata.
- Do not retry malformed responses, invalid requests, authentication failures, policy rejections, or tool execution after an uncertain side effect.
- Use bounded exponential backoff with jitter, per-request retry budget, and a global retry budget.
- Add per-provider circuit breakers and half-open probes; avoid synchronized retry storms.
- Make fallback a task-level policy with explicit data classification, user/tenant consent, allowed destination, and audit record.
- A provider outage must never silently change data residency or privacy routing.

## 7. Provider switching and in-flight requests

### `providers/runtime_manager.py`

Observed:
- Uses an in-process reentrant lock while selecting providers.
- Builds a candidate provider before mutating active configuration.
- Persists the provider identifier atomically with restrictive file permissions.
- Rejects unimplemented or unready selections and rejects corrupt persisted state.

Potential lifecycle gap:
- The manager lock serializes selection and catalog access, but provider calls through the shared adapter are not coordinated by that lock.
- The previous provider is replaced without a visible drain/close lifecycle in the inspected manager.
- In-flight requests may continue using the old provider while new requests begin using the new one; ownership and disposal of the old adapter are not explicit.

Required:
1. Represent the active provider as a versioned immutable runtime handle.
2. Acquire a per-request reference/lease before inference begins.
3. On switch, publish the new handle atomically, stop assigning new work to the old handle, then drain old in-flight references.
4. Close the old provider only after drain and only if the manager owns it.
5. Roll back selection if candidate construction or persistence fails; test each failure point.
6. Record provider switch actor, reason, old/new provider IDs, config version, and timestamp without secrets.
7. Define behavior when a provider is switched while a stream is open.

## 8. Hybrid routing and privacy policy

### `providers/hybrid.py`

Observed:
- General generation defaults to local.
- Cloud routing is opt-in by normalized task name.
- Missing/unavailable local provider does not fall back to cloud.
- Child provider ownership is explicit through `owns_providers`.
- Hybrid wrapper has idempotent close behavior when it owns children.

Important capability limitation:
- Hybrid implements the text generation contract. The provider registry tests explicitly verify that selected hybrid fails closed for structured agent chat rather than pretending it supports the capability.

Recommendations:
- Keep text-only and structured-agent capability separate in the catalog.
- If hybrid agent chat is added, route the entire conversation and tool proposal flow under one explicit policy; do not split turns between providers without a privacy and context-transfer decision.
- Use task classification that cannot be set by untrusted prompt text alone.
- Record the route decision and data classification in an audit event.
- Add tests for case normalization, unknown task names, policy mutation, unavailable cloud, and attempted prompt-driven route escalation.

## 9. Token usage, budgets, and cost governance

### `core/token_usage.py`

Observed:
- Tracks provider/model, input/output token counts, exact versus estimated events, session totals, and threshold/spike alerts.
- Bounds event count at 10,000 and validates token counts.
- Persists to a local JSON file using a temporary file and atomic replace.
- Uses a process-local reentrant lock.

Limitations:
- A process-local lock does not protect a shared usage file from concurrent processes; writes can overwrite each other's events.
- When the event cap is reached, recording fails until the log is archived or cleared.
- Token totals and threshold alerts are not monetary cost accounting; no provider price catalog or currency conversion is applied here.
- The tracker is not a tenant-scoped, tamper-evident billing ledger.

Recommendations:
1. Move production usage events to the durable database with tenant, user, request, agent, provider, model, and pricing-version identifiers.
2. Persist provider-reported usage when available; retain estimates as estimates and track source/confidence.
3. Version pricing tables and calculate cost separately from token totals.
4. Enforce hard per-request, per-user, per-tenant, and global budgets before dispatch and while streaming.
5. Reserve estimated spend before inference; reconcile against actual usage after completion.
6. Define behavior for missing usage metadata and interrupted streams.
7. Add idempotency keys to usage events so retries cannot double-charge.
8. Keep usage records append-only and auditable; separate analytics retention from financial retention.

## 10. Test and verification plan

Existing focused tests include:
- `tests/test_openai_compatible_tool_calls.py`: tool proposal normalization and malformed tool rejection.
- `tests/test_hybrid_providers.py`: local-first routing, no implicit cloud fallback, endpoint restrictions.
- `tests/test_provider_factory.py`: provider selection, configuration, timeout validation.
- `tests/test_provider_runtime_manager.py`: persisted selection, atomic rejection of unready providers, corrupt state.
- `tests/test_provider_agent_adapter.py`: structured adapter and streaming contract.
- `tests/test_gemini_cli_provider.py`: subprocess bounds, timeout, process termination, sanitized failures.
- `tests/test_provider_catalog.py`: readiness metadata and secret redaction.
- `tests/test_token_usage.py`: token validation, exact/estimated counts, persistence, alerts.

Add:
1. Slow-trickle SSE and NDJSON exceeding absolute deadline while staying under inactivity timeout.
2. Cancel during active stream; assert socket/response is closed promptly.
3. Abandoned stream iterator; assert underlying response closes.
4. Provider switch while request and stream are in flight; assert old provider drains and closes exactly once.
5. Candidate provider construction and persistence failure; assert active provider/config remain consistent.
6. Concurrent token usage writes from multiple processes; assert no lost events.
7. Budget reservation/reconciliation under timeout, cancellation, retry, and partial response.
8. Retry classification for 400/401/403/408/409/429/5xx and network failures.
9. Circuit-breaker open/half-open/close behavior and probe isolation.
10. Prompt-driven attempt to alter hybrid routing; assert policy remains server-controlled.
11. DNS/egress policy tests for configured cloud hosts and remote local endpoints.
12. Tool-call stream truncation or malformed terminal event; assert no tool dispatch before final validation.

## 11. Prioritized remediation

| Priority | Work item | Evidence required |
|---|---|---|
| P0 | Enforce absolute inference deadlines and active-stream cancellation | Slow-trickle and cancel-in-read tests |
| P0 | Coordinate provider replacement with in-flight requests and disposal | Concurrent switch/drain tests |
| P1 | Define versioned capability contracts | Provider contract suite across adapters |
| P1 | Add centralized retry classification and circuit breakers | Fault-injection suite |
| P1 | Move usage accounting to durable multi-process storage | Concurrent-write and idempotency tests |
| P1 | Add token/cost budgets and reservation/reconciliation | Budget enforcement integration tests |
| P1 | Add provider concurrency limits and backpressure | Saturation/load tests |
| P2 | Strengthen DNS/egress controls and remote Ollama trust policy | Deployment network tests |
| P2 | Expand stream lifecycle and malformed-event tests | Streaming contract suite |

## Verification status

This document is based on targeted source and test inspection. No tests were executed. No live cloud or local provider calls, billing checks, streaming fault injection, concurrency/load tests, DNS/egress tests, or provider-switch experiments were performed. Provider behavior under actual vendor-specific rate limits, token accounting, and deployment proxy configuration remains unverified.
