# Phase 208 — unified governed provider dispatch

- Added GovernedProviderExecutor to wrap the normalized ProviderAgentAdapter / ProviderGateway call boundary.
- Dispatch order: reserve circuit admission, reserve global/provider/agent request budget, execute, record outcome, release budget in a finally block.
- Open circuits reject before provider dispatch.
- Budget rejection releases a half-open probe reservation.
- Provider exceptions count as failures; cooperative cancellation is not treated as provider unhealthiness.
- Capacity is released on success, provider exception, cancellation, and adapter validation errors.
- No retry, provider fallback, or alternate-provider selection is performed.

## Important limits

- This is an opt-in wrapper and is not yet injected into every runtime/worker path.
- Circuit and budget state remain process-local; multi-worker deployments require shared atomic storage.
- The wrapper does not make legacy blocking provider calls interruptible. Existing adapter deadline semantics still apply.
- The request-count budget does not meter tokens, billing, or provider-side quotas.
- Callers must use a stable provider ID accepted by the circuit breaker and a stable agent ID accepted by the budget.
