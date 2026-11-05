# Phase 210 — durable governed provider execution

- Added DurableGovernedProviderExecutor, an explicit provider dispatch wrapper backed by SQLiteProviderGovernanceStore.
- Request contract validation runs before quota consumption.
- Durable admission reserves cross-process global/provider/agent capacity and checks persistent circuit state.
- Provider success, ordinary exceptions and cooperative cancellation are persisted against the exact lease.
- Provider failures update the shared circuit; cancellation releases the reservation without a health penalty.
- Admission failures are normalized as DurableProviderAdmissionRejected.
- The executor rejects configurations where the lease timeout is not greater than the adapter's declared default timeout.
- Tests cover success, failure, cancellation, second-store admission enforcement, validation-before-quota and timeout configuration.

## Operational boundaries

- This is opt-in. Existing GovernedProviderExecutor and runtime paths are unchanged; callers must explicitly construct this executor.
- Every coordinating process must use the same SQLite database on a filesystem with reliable SQLite locking.
- The lease timeout must exceed the real maximum execution time, including any caller-supplied execution deadline. Adapter default timeout alone cannot bound every legacy or caller-specific blocking operation.
- A worker that outlives its lease may still be executing remotely after another worker reclaims capacity. Lease expiry is not remote cancellation or fencing; strict protection against stale workers requires provider-side idempotency/fencing or a shared network database protocol.
- If durable outcome recording itself fails, the storage error is surfaced. The lease then remains until expiry and must be reconciled operationally.
- No retries, fallback, or mock providers are introduced.
