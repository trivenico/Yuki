# Streaming Consumer Exception-Path Cleanup Audit

## Scope

Source-only review at PR #1346 head `55c073b56f82e706533912f10f3ce2952315d439`.

Reviewed:

- `core/agent_execution_loop.py`: stream iteration, cancellation/deadline handling, event validation, text callback invocation
- `providers/agent_adapter.py`: provider generator forwarding and provider lease scope
- `providers/openai_compatible.py`: SSE response context lifetime
- `tests/test_agent_execution_loop.py`: streaming, cancellation, and duration-limit coverage
- `tests/test_provider_runtime_manager.py`: provider lease lifetime while a stream is open

No tests or live provider calls were executed.

## Finding: streaming consumer does not explicitly close the iterator on every exceptional exit

**Severity: Moderate (P2) resource-lifecycle reliability issue**

### Evidence

1. `AgentExecutionLoop.run()` obtains an iterator from `self.stream_model(...)` and advances it with `next(stream_events)`.
2. When cancellation or the loop duration budget is detected between events, the loop explicitly calls the iterator's `close()` method when available.
3. The regular event-processing path has no surrounding `try/finally` that closes the iterator on all exits.
4. That path can exit exceptionally after receiving an event, including:
   - `on_text_delta(delta)` raising from a caller-provided callback;
   - a non-mapping event;
   - an unsupported event type;
   - an invalid or oversized text delta;
   - duplicate or malformed final response events;
   - a mismatch between streamed text and final provider text.
5. `ProviderAgentAdapter._leased_stream()` wraps the provider stream in `with manager.lease_adapter(...)` and forwards it using `yield from`. The lease is released when this generator is exhausted or closed.
6. The OpenAI-compatible SSE provider keeps the HTTP response inside a generator-scoped response context manager. Closing the generator unwinds that context and closes the response.
7. Existing tests verify explicit closure on cancellation/duration stop and verify a provider lease remains active while a stream is open. They do not cover callback or event-validation exceptions while the provider generator is suspended at a yield.

### Impact

When the consumer fails while the provider generator is suspended after yielding an event, the consumer does not explicitly close that generator. Prompt release of the provider lease and underlying response therefore depends on generator finalization rather than a deterministic cleanup path.

In common CPython execution, dropping the last reference often finalizes a generator promptly, but relying on that behavior is weaker than explicitly closing the iterator and is not a portable resource-ownership contract. If finalization is delayed, the provider lease can remain active and provider shutdown can continue to refuse cleanup while the response remains open.

This is especially relevant when the text callback is an application/UI integration boundary and can fail independently of the provider transport.

### Recommended remediation

1. Put stream consumption inside a `try/finally` and call `close()` on the iterator in the `finally` block whenever a callable close method exists.
2. Keep the existing explicit cancellation/deadline behavior, but centralize closure so all paths share one idempotent cleanup path.
3. Preserve the original callback/validation/provider exception if stream closure also fails; record cleanup failure separately rather than masking the primary error.
4. Add deterministic tests with a generator that records its `finally` execution:
   - text callback raises after the first delta;
   - invalid event arrives after a yielded delta;
   - final response mismatch occurs after a yielded delta;
   - provider iteration itself raises;
   - cancellation and duration-limit paths still close exactly once.
5. Add an adapter-level test proving that exceptional consumer exit releases the provider lease and permits provider shutdown after the generator is unwound.

## Existing protections and boundaries

- Normal stream exhaustion closes the provider generator through normal generator completion.
- The cancellation/deadline branch explicitly closes the iterator.
- The adapter's context-manager lease and provider response context manager correctly unwind when their generator is closed or exhausted.
- If the provider generator itself raises, its own stack unwinds and its response context manager exits.

The finding is specifically about consumer-side exceptions that occur while the provider iterator is suspended at a yield. This review does not establish a persistent leak in all Python runtimes or prove that a provider request continues after every callback error.

## Verification status

No runtime code was changed. Tests, CI, and live provider behavior were not exercised.
