# Phase 198 — Provider-aware tool idempotency contract

## Goal

Make tool invocation idempotency explicit and provider-aware without implying that Yuki can guarantee exactly-once effects for arbitrary external systems.

## Contract

A guarded handler may opt into provider idempotency only by setting:

```python
handler.supports_idempotency_key = True
```

It must also accept the keyword-only `idempotency_key` parameter (or `**kwargs`). Yuki verifies both the explicit declaration and callable signature before forwarding a key. A malformed declaration fails closed before the handler is invoked.

Handlers without the explicit capability continue receiving only their original argument. Yuki never guesses provider support from a function signature.

## Key lifecycle

- Yuki derives a deterministic SHA-256 key scoped to the authenticated owner, conversation, generation request, and model tool-call ID.
- The key and capability declaration are persisted with the durable tool execution record before dispatch.
- The same key is forwarded to a handler only when its explicit capability contract is valid.
- Duplicate call IDs with different argument fingerprints or idempotency keys are rejected.
- The key is suitable for a provider's documented idempotency field only when the handler maps it to that provider's supported API contract and respects the provider's retention and payload-matching rules.

## Recovery and safety

- Handler exceptions remain `uncertain`; they do not prove that the provider rejected the action.
- Yuki does not automatically retry uncertain calls merely because a handler declares support.
- A future supervised retry path must reuse the exact persisted key and argument fingerprint, verify provider-specific retention guarantees, and require explicit authorization where the action is consequential.
- Providers without documented idempotency semantics remain non-retryable automatically.
- This mechanism does not claim global exactly-once execution. It provides a stable key and a contract boundary that provider adapters can implement.

## Validation

Regression coverage verifies explicit capability opt-in, signature validation, no key forwarding to undeclared handlers, durable key persistence, and end-to-end forwarding through the guarded conversation execution path.
