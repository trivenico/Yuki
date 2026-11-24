# WI-6U13J-D — Fake-provider crash and reconciliation harness

## Test-only provider

`FakeExternalProvider` is deterministic and test-only. It accepts an idempotency key and request digest, returns a stable provider reference/result digest for repeated identical requests, rejects reuse of a key with a different digest, and can simulate a timeout or worker crash after acceptance. It has no network access and stores no provider payload.

## Recovery cases covered

- An intent created before any claim remains durably pending; repeating the same intent request returns the same operation and does not call the provider.
- A provider acceptance followed by a simulated timeout leaves the operation inflight until its operation lease expires. The next scoped claim transitions it to `unknown`, never requeues it, and does not invoke the fake provider again.
- The fake provider lookup resolves the accepted operation. An explicitly authorized operator reconciliation records the result digest and provider reference and transitions the unknown operation to its terminal succeeded state.
- Reusing a provider idempotency key with a different request digest is rejected.

## Safety conclusions

The harness verifies durable intent and conservative unknown-outcome handling; it does not establish exactly-once provider execution. Real provider adapters remain disabled until a concrete provider's idempotency and lookup semantics, authorization policy, and operational reconciliation procedure are reviewed. No production dispatcher or provider credentials are introduced by this stage.
