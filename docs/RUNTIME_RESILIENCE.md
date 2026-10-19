# Runtime resilience and self-healing

These standard-library helpers are building blocks; they are not automatically wired to restart operating-system services.

- `retry_async` retries only declared transient exceptions, is bounded, and preserves asyncio cancellation.
- `CircuitBreaker` is process-local and must be created per dependency. It is not a distributed lock.
- `SelfHealingManager` can invoke only callbacks explicitly registered by trusted application code. Each callback is timeout-limited and cooldown-limited.
- Recovery callbacks must be narrowly scoped, idempotent where possible, and never execute model-generated shell commands.
- Health probes should decide when to request recovery; after recovery, verify health again and report the observed state.
- Production wiring must ensure a recovery callback cannot restart arbitrary processes, delete user data, change firewall policy, or access secrets without a separate approved policy.
