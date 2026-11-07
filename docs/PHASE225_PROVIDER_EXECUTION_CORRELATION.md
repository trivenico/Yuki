# Phase 225 — provider execution correlation

- DurableGovernedProviderExecutor accepts an optional AgentExecutionTraceStore and optional parent `_trace_execution_id`.
- After provider admission, it creates a provider child span carrying the exact durable provider execution ID.
- Provider started/completed/failed events update the trace lifecycle; cancellation is marked cancelled, and lease-loss-after-provider-success is marked uncertain.
- Trace failures are best-effort and never trigger provider retries or alter provider governance decisions.
- Trace records contain identifiers and lifecycle types only; prompts, requests, responses, credentials and exception text are excluded.

Limitations: provider admission and trace creation use separate SQLite databases. A crash between them can leave an uncorrelated provider execution. Parent trace IDs must be supplied explicitly. Duplicate admission is still returned to the caller without an extra trace event. No live provider was exercised.
