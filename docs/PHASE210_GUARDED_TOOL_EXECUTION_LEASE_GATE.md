# Phase 210 — guarded tool execution lease gate

## Change

`GuardedToolRuntime.invoke` accepts an optional synchronous `execution_guard` callback. When supplied, it is evaluated after the normal tool policy decision and immediately before the registered handler is invoked. Only the literal boolean `True` permits dispatch. A false result, invalid callback, or callback exception fails closed and records a bounded audit outcome.

## Contract and limits

This is an enforcement hook at the local tool-dispatch boundary. It does not itself establish worker identity, issue fencing epochs, prove process termination, or make a remote side effect atomic with a lease check. The caller must provide a trusted guard that validates current ownership against authoritative state.

A check immediately before a handler narrows the stale-worker window but cannot revoke a side effect already in progress. High-impact external adapters must validate revocable execution credentials and fencing generations at their own commit boundary, ideally using an idempotency key and a transactional/outbox protocol.

## Integration status

The existing durable skill worker has not yet been wired to supply this callback. That integration must not use worker ID alone as a fencing identity: the durable task lease needs a unique claim token/generation returned by `claim`, and heartbeat/complete/fail must require that exact token. This is intentionally recorded as unfinished rather than implying the hook is already protecting durable workers.

## Validation

Regression tests cover successful guard approval, denial, callback exceptions, and invalid callback values. Exact-head CI and downstream worker integration remain required.
