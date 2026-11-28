# Provider inference ambiguous-outcome and retry audit

## Scope

Source review at PR #1346 head `c318e0fb8bbc2feed0ceed1199d57f4934fc1da8`. Reviewed:

- `providers/openai_compatible.py`
- `providers/agent_adapter.py`
- `providers/factory.py`
- `automation/specialist_worker.py`
- `automation/task_runtime.py`
- `automation/task_retry.py`
- `automation/workflow_store.py`
- provider and specialist worker test definitions

This is a source-level audit. Tests and live provider requests were not executed.

## Finding: provider inference requests have no idempotency or durable outcome reconciliation

**Severity: Moderate (P2) — duplicate provider work and billing**

### Evidence

1. `OpenAICompatibleProvider.generate()` and `generate_chat()` submit HTTP `POST` requests to `/chat/completions`.
2. The request includes model, messages, tools/tool choice and authorization, but no stable request id or idempotency key. The provider adapter does not persist a request receipt before dispatch.
3. Transport exceptions including `URLError`, `TimeoutError`, and `OSError` are collapsed into a generic `RuntimeError`. The adapter cannot tell whether the request was rejected before dispatch, accepted and still running, or completed with a response that was lost.
4. The specialist worker catches handler exceptions and records a terminal `specialist_handler_failed` error. The error code does not preserve an ambiguous remote outcome.
5. `TaskRuntime.retry_failed()` allows an authorized retry of failed work by creating a new run and new idempotency key. This protects task-row creation from duplicate retries, but it does not deduplicate the provider-side operation across the original run and its retry.
6. The OpenAI-compatible adapter's non-streaming and streaming calls both lack a Yuki-owned request identity and provider-side idempotency/reconciliation contract.

### Impact

A provider may complete inference and charge for it even though Yuki never receives the response. Yuki then records a failed task. An authorized retry dispatches a second inference request, potentially repeating billable work and generating a different answer.

This is currently an inference/cost and result-consistency issue, not evidence that the provider executes Yuki tools remotely. The adapter validates returned tool calls and the agent loop dispatches tools locally after receiving a response. The duplicate inference may nevertheless produce different tool-call proposals, and future handlers with external side effects would require stronger end-to-end idempotency.

### Existing safeguards and limits

- Provider calls have bounded request timeouts and response-size limits.
- Cloud endpoints require HTTPS and an exact trusted hostname; redirects are rejected.
- Task retry creation is transactionally idempotent for a given source task/version.
- The provider adapter validates the returned response and tool-call envelope.
- None of these controls proves whether a timed-out remote POST completed or prevents a second POST for the same logical operation.

### Recommended remediation

1. Persist a provider-operation record before dispatch, keyed by a stable logical operation ID derived from the task run/step and a request digest. Do not use a fresh retry-run ID as the sole deduplication identity.
2. Where the provider explicitly supports idempotency keys, send the same stable key for every transport retry of that exact request. Verify provider-specific retention, scope, and payload-mismatch semantics; do not assume all OpenAI-compatible endpoints honor the same behavior.
3. Where provider-side idempotency or request lookup is unavailable, record timeout-after-dispatch as `outcome_unknown`, not ordinary `failed`. Require reconciliation or an explicit operator decision before issuing another request.
4. Persist a bounded, redacted receipt on definitive response: provider, model, request digest, logical operation ID, provider request ID when supplied, timestamps, and outcome. Never persist credentials or raw prompts by default.
5. Separate transport retries from task retries. A transport retry must reuse the same operation identity; a new logical attempt must require an explicit policy decision when the previous outcome is unknown.
6. Add deterministic fake-transport tests for: server accepts and completes then client times out; connection fails before request dispatch; provider returns a definitive error; idempotent retry with same key; mismatched payload under same key; and a task retry blocked while outcome remains unknown.

### Non-findings

- This audit did not find evidence that Yuki automatically retries the provider HTTP request internally.
- Task-level retry creation is protected against duplicate child runs in the shared SQLite store. The gap is provider-operation deduplication across distinct task runs.
- No claim is made that every OpenAI-compatible endpoint supports idempotency keys. This must be verified per provider and endpoint.

## Audit limitations

No live provider calls, fault injection, tests, or CI jobs were executed. The finding is based on source inspection and should be validated with deterministic transport tests before release.
