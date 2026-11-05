# Phase 221 — automatic worker instrumentation

## Delivered

- Optional integration of `AgentWorker` with the durable agent execution trace store.
- Worker task claim creates a trace with task and registered task-type identifiers.
- Worker start, completion, failure, cancellation, and missing-handler outcomes emit allow-listed lifecycle events.
- Trace records contain identifiers and lifecycle status only; task payloads, handler results, prompts, exception messages, and credentials are never copied into telemetry.
- Telemetry persistence is best-effort and does not alter task execution if the trace store is unavailable. A bounded in-memory error counter exposes instrumentation failures to the owning process.

## Boundaries

This instruments the `AgentWorker` execution boundary only. It does not yet instrument every coordinator plan, provider gateway, or tool invocation. Parent/root trace propagation and provider execution ID correlation remain future integration work. Token and cost metrics are not inferred from task payloads or model responses.

## Deployment

The trace store uses SQLite and local filesystem locking. Multi-host deployments need a transactional shared database or telemetry service. The caller must explicitly pass the trace store; existing worker construction remains compatible.
