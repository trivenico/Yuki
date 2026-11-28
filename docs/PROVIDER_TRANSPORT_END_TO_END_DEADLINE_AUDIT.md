# Provider Transport End-to-End Deadline and Stream Lifetime Audit

## Scope

Source-only review at PR #1346 head `222434defe57e8db1028c5c6fead83ec9b08cd13`.

Reviewed:

- `providers/openai_compatible.py`: non-streaming `generate()`, `generate_chat()`, and SSE `generate_chat_stream()`
- `providers/agent_adapter.py`: request and generator lease handling
- `providers/runtime_manager.py`: provider lease and shutdown behavior
- `tests/test_provider_runtime_manager.py`: request and stream lease lifetime coverage

No tests or live provider calls were executed.

## Finding: configured provider timeout is an inactivity timeout, not a total operation deadline

**Severity: Moderate (P2) reliability/resource-exhaustion risk**

### Evidence

1. `OpenAICompatibleProvider` validates and stores a timeout between 1 and 120 seconds.
2. Each HTTP operation calls `opener.open(request, timeout=self.timeout)`. The same timeout is used for non-streaming response reads and streaming response reads.
3. The non-streaming methods call `response.read(2_000_001)`. The size cap bounds bytes, but there is no monotonic deadline checked during the read.
4. The streaming method repeatedly calls `response.readline(65_537)`. It enforces a maximum line size, a 2 MB total byte limit, and a 24,000-character text limit. It does not track elapsed wall-clock time or stop at a total deadline.
5. Python's socket timeout used by urllib is an inactivity timeout for blocking socket operations, not a total deadline for the complete response. A server that periodically sends enough data to keep reads from timing out can keep the operation alive beyond the configured timeout, subject to the byte/line caps.
6. `ProviderAgentAdapter._leased_stream()` holds the provider-manager lease for the entire generator iteration. The lease is released when the stream exits or is explicitly closed. The provider manager refuses shutdown while any such lease remains active.

### Impact

A slow or stalled-but-periodically-progressing provider response can exceed the operator's configured timeout and occupy a worker/request thread and provider lease for substantially longer than expected. This can delay application shutdown because provider cleanup correctly waits for active leases.

For SSE, the stream's byte and line caps limit the amount of data but do not bound elapsed time. For non-streaming responses, the response size cap similarly does not bound the time spent receiving that response.

A consumer that pauses while holding a yielded stream also keeps the generator and provider lease alive until iteration resumes or the stream is closed. This is consistent with the current streaming lease contract, but it makes explicit generator closure important for callers that abandon a stream.

### Existing protections

- HTTPS endpoint allowlisting and redirect rejection protect endpoint trust.
- Response size, SSE line size, total stream bytes, and accumulated text are bounded.
- Provider leases prevent a provider from being closed while an active request or stream still uses it.
- The stream is wrapped in a response context manager, so normal completion, exceptions, or explicit generator closure unwind the response context.
- Existing tests cover provider shutdown refusal during an active chat request and while a stream remains open, then successful close after stream closure.

These controls reduce other risks but do not establish a total operation deadline.

## Recommended remediation

1. Add an explicit end-to-end deadline based on a monotonic clock, beginning before opening the request and covering connection establishment, response headers, body reads, and all SSE events.
2. Before each blocking read, set the socket/read timeout to the smaller of the configured inactivity timeout and remaining deadline. Recompute the remaining duration after every read. If urllib's abstraction cannot safely support per-read deadline adjustment, use a transport with explicit total-timeout support or a carefully bounded socket layer.
3. Apply the same deadline policy to `generate()`, `generate_chat()`, and `generate_chat_stream()`; avoid giving streaming a different and unbounded wall-clock policy.
4. Ensure timeout closes the response and propagates a distinct timeout/ambiguous-outcome error. Do not automatically retry a timed-out POST without the provider idempotency protections discussed in the existing ambiguous-outcome audit.
5. Make stream ownership explicit for consumers: document mandatory closure, use context-managed stream APIs where practical, and ensure cancellation/abandonment closes the underlying generator promptly.
6. Add deterministic local HTTP-server tests:
   - drip bytes at intervals shorter than the inactivity timeout and verify the total deadline still terminates the operation;
   - stall without sending bytes and verify the inactivity timeout;
   - exceed byte/line caps and verify response closure;
   - close a partially consumed generator and verify the provider lease is released;
   - verify timeout releases the provider lease and permits shutdown after the operation has actually unwound.

## Boundaries and confidence

The absence of a total elapsed-time deadline is directly visible in the inspected implementation. The slow-drip behavior follows urllib/socket timeout semantics; it was not reproduced against a live or local server in this review.

The audit does not claim that a response can exceed the byte caps, that the provider manager closes an active provider, or that every stream leaks. Existing lease and response-context protections remain valuable.

No runtime code was changed. Tests, CI, and live provider behavior were not exercised.
