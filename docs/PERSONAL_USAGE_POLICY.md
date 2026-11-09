# Personal owner usage policy

Yuki is a personal, owner-operated assistant. Its application layer must not impose a subscription, credit balance, daily/monthly allowance, lifetime token quota, or arbitrary request-count quota on the configured owner.

## Policy

- No Yuki-enforced daily, monthly, or lifetime token allowance.
- No token-based credits, paywalls, or subscription tiers for the personal owner.
- No usage-count limit that stops otherwise valid owner requests.
- Usage thresholds are advisory telemetry only. They may notify the owner but must never reject a request.
- Keep usage history durable without imposing an event-count quota.
- Prefer a configured local model when the owner wants to avoid per-token cloud API charges.
- Show provider, model, exact-versus-estimated token counts, and known provider costs where available.

## Limits that still apply

"Unlimited" means no artificial Yuki usage allowance; it does not mean infinite compute or unlimited provider entitlements.

- Cloud providers retain their own account quotas, rate limits, context windows, output limits, and billing. Yuki cannot override them.
- Local inference is constrained by installed model context size, RAM, VRAM, CPU/GPU throughput, storage, and power.
- Per-request payload bounds, response-size bounds, tool-count bounds, timeouts, concurrency controls, approval gates, and recursion/step guards are safety and reliability controls, not token allowances. They must be explicit, configurable where safe, and must not be silently described as unlimited context.
- Provider backoff and fallback may only use providers the owner has configured and authorized. Do not bypass provider rate limits or retry indefinitely.
- Runaway agent loops, unbounded task spawning, and uncontrolled parallelism remain prohibited. The owner may configure workload ceilings to protect their machine and cloud spend.
- SQLite stores an individual event in a signed 64-bit integer column. This is a database representation boundary, not a product token allowance; lifetime aggregate counters use decimal text and Python arbitrary-precision integers.

## Implementation status

The `core/token_usage.py` tracker stores usage events in an append-only SQLite ledger rather than a fixed-size JSON list. The former 10,000-event limit and 10-million-token per-event application ceiling are removed. Advisory threshold/spike signals never reject recording. Lifetime totals are maintained transactionally as decimal text to avoid signed-64-bit aggregate overflow, and existing JSON usage logs are imported once without overwriting the source.

The provider-neutral `ProviderAgentAdapter` can now receive an injected usage tracker and records both non-streaming and streaming chat completions. OpenAI-compatible chat responses and Ollama chat responses expose exact token counts when their response metadata includes both input and output counts. Providers without exact metadata use an explicitly marked character-based estimate. Usage persistence failures are surfaced through `last_usage_error` and an optional callback, but do not fail an otherwise successful inference. The application composition must still inject a durable tracker into the adapter; until that wiring is configured in the running application, end-to-end metering is not active. Cost calculation and dashboard display remain follow-up work.

This policy does not claim that cloud inference is free or that any model has an infinite context window.
