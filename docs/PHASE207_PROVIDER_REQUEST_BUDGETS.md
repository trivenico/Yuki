# Phase 207 — provider request budgets and admission control

- Added process-local, thread-safe request admission across global, per-provider, and per-agent concurrency limits.
- Added a fixed-window per-agent request quota that remains consumed after the in-flight reservation is released.
- Reservations use opaque lease IDs, idempotent release, and bounded expiry to reclaim abandoned in-process reservations.
- Snapshot API exposes active counts and the current agent request-window usage without prompt or credential data.
- Limits and identifiers are validated and bounded.

## Operational boundaries

- This component is not yet wired into provider dispatch; callers must acquire immediately before dispatch and release in a finally block.
- Expired leases are reclaimed only when another acquire/snapshot occurs; there is no background sweeper.
- State is process-local and resets on restart. Multi-process or multi-host deployments require an atomic shared backend.
- Request-count quotas do not measure tokens, GPU time, money, or provider-side rate limits.
- A lease expiry can reclaim local capacity while a legacy blocking provider call is still running; production callers must set lease timeout above the maximum enforced execution deadline and account for uncertain outcomes.
