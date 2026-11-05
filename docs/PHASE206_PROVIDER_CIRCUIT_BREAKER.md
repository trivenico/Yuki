# Phase 206 — provider circuit-breaker primitive

- Added a thread-safe per-provider circuit breaker with closed, open, and half-open states.
- Configurable failure threshold and bounded reset timeout.
- Monotonic clock prevents wall-clock changes from altering cooldown behavior.
- Exactly one concurrent half-open probe is admitted; successful probes close and reset the circuit, failed probes reopen it.
- A caller can release a reserved probe after cancellation before an outcome is known.
- State snapshots expose provider ID, state, consecutive failure count, and retry delay only.

## Integration and operational boundaries

- The caller must invoke allow_request before dispatch and record_success/record_failure after a definitive provider outcome; release_probe is for cancellation without an outcome.
- This primitive does not execute providers, detect failures, retry, route to another provider, or guarantee a provider request is interrupted.
- Do not record policy denials or local validation errors as provider failures. Treat timeouts with uncertain remote outcomes according to the operation reconciliation policy.
- State is process-local and resets on restart. A shared multi-worker deployment needs a durable, atomic backend before claiming fleet-wide circuit enforcement.
- Tests exercise state transitions and concurrency using an injected clock; no mock provider or live provider request is involved.
