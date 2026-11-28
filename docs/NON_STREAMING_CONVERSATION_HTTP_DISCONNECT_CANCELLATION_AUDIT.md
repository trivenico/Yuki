# Non-Streaming Conversation HTTP Disconnect Cancellation Audit

## Summary

**Confirmed cancellation-propagation gap:** the ordinary `POST /v1/conversations/{id}/messages` endpoint invokes `ConversationService.reply()` through `asyncio.to_thread()` without a cancellation callback or a disconnect watcher. The streaming endpoint has a separate disconnect watcher that sets a cancellation event and passes it to the service.

Consequently, disconnecting a client from the non-streaming endpoint does not signal the synchronous generation worker to stop. Cancelling the ASGI coroutine also does not stop a function already executing in a worker thread. Generation may continue consuming provider resources and may persist an assistant response after the client has gone away.

## Source evidence

- `api/conversations.py`
  - The non-streaming message route awaits `asyncio.to_thread(self.conversations.reply, ...)` and does not pass `cancellation_requested`.
  - The streaming route delegates to `_stream_reply()`.
  - `_stream_reply()` creates a per-request `threading.Event`, registers it in `_active_generations`, watches ASGI `http.disconnect`, and sets the event when the client disconnects.
  - The streaming worker passes `cancellation_requested=cancellation.is_set` to `ConversationService.reply()`.
  - The explicit `/messages/cancel` endpoint looks up only entries in `_active_generations`; the non-streaming route does not register an entry there.
- `core/conversation_service.py`
  - The service combines its caller-provided cancellation predicate with lease-loss state and passes it to the agent loop.
  - If no predicate is provided, there is no client-cancellation signal to propagate.
- `tests/test_api_runtime.py`
  - The inspected runtime tests cover shutdown drain and ASGI task cancellation accounting, not cancellation of the synchronous conversation worker after a client disconnect.
  - The existing streaming cancellation path is structurally distinct from the non-streaming route.

## Failure sequence

1. A client sends a non-streaming message request.
2. The API starts `ConversationService.reply()` in a worker thread via `asyncio.to_thread()`.
3. The client disconnects while the provider/model operation is in progress.
4. The non-streaming route has no concurrent `receive()` watcher and no cancellation event registered for this request.
5. The worker thread continues through generation and may persist the assistant message and terminal generation status.
6. The disconnected client cannot receive the result; a subsequent retry must rely on request-ID idempotency/replay behavior.

Even if the ASGI server cancels the request task, cancelling an await of `asyncio.to_thread()` does not forcibly stop the already-running synchronous function in its worker thread.

## Impact and boundaries

- Disconnected non-streaming requests can continue using provider capacity and application worker threads.
- The operation may complete and persist a response that no client is waiting to receive.
- A caller retrying with the same request ID should use the existing replay behavior rather than cause a second model/tool sequence; callers that generate a new request ID may submit a distinct request after the first turn has completed.
- This finding does not prove that every disconnect causes a duplicate operation, nor that synchronous provider I/O can be forcibly interrupted by setting a Python event.
- The streaming route's event-based cancellation is better integrated, but provider calls already blocked in synchronous I/O still require transport-level cancellation or timeout support. The existing provider deadline audit covers the lack of a total provider operation deadline.

## Recommended remediation

1. Give non-streaming requests the same per-request cancellation lifecycle as streaming requests: create and register a cancellation event before starting the worker, and pass its `.is_set` predicate into `ConversationService.reply()`.
2. Monitor ASGI disconnect while the worker runs. On disconnect, set the event and retain worker ownership until it exits; do not assume cancellation of the asyncio task stops the thread.
3. Make `/messages/cancel` work for both streaming and non-streaming requests, with consistent authenticated ownership and request-ID binding.
4. Preserve idempotent replay semantics. A cancellation request should not erase an already-persisted assistant answer or automatically re-run potentially side-effecting work.
5. Propagate cancellation into provider transports that support active request/stream closure. A cooperative event alone cannot interrupt a blocking synchronous provider call that does not observe it.
6. Add deterministic ASGI tests:
   - Start a non-streaming request with a blocking fake model and disconnect the client after the model starts; assert the cancellation predicate is set.
   - Exercise `/messages/cancel` against a non-streaming request and verify it targets only the authenticated owner's matching request.
   - Verify the worker is awaited/reaped and no unobserved background task/thread is created.
   - Verify cancellation before tool dispatch prevents dispatch, while a tool already in flight is not falsely represented as forcibly stopped.
   - Retry with the same request ID and verify the existing persisted result is replayed without a second model/tool invocation.

## Severity and confidence

- **Severity:** Moderate (P2), resource usage and cancellation-semantics gap.
- **Confidence:** High for the endpoint asymmetry and missing cancellation propagation.
- **Production occurrence:** Not established; no live ASGI server or provider was exercised.

## Review limitations

Source inspection at PR #1346 head `87b7b271a54acc779a601a28771c96a456c7f447`. No runtime code was changed, no tests were executed, and no production telemetry was inspected.
