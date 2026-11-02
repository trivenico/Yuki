# Phase 155 — Authenticated cooperative generation cancellation

## API

An authenticated client can request cancellation for an active conversation generation:

```http
POST /v1/conversations/{conversation_id}/cancel
Authorization: Bearer <session-token>
```

Response:

```json
{"cancellation_requested": true}
```

The endpoint verifies conversation ownership before revealing active-generation state. If no generation is active, it returns `false`. A second streaming request for the same conversation on the same API instance receives HTTP 409.

## Execution behavior

- Cancellation is scoped to one conversation execution, not a process-global mutable callback.
- The API stores a per-generation `threading.Event` and passes its predicate through ConversationService to AgentExecutionLoop.
- The agent checks cancellation before model turns, between streamed provider events, after provider completion, and before guarded tool dispatch.
- On streamed cancellation, the provider iterator is closed when it supports `close()`.
- The service persists an explicit `cancelled` assistant outcome so the conversation does not remain with an orphaned user message.
- The SSE endpoint emits an `event: cancelled` terminal event with the persisted result.

## Important limitations

This is cooperative cancellation. It cannot interrupt a synchronous provider call while that call is blocked waiting for network I/O; provider timeouts still bound that interval. Closing a streaming iterator occurs at the next yielded event.

The active cancellation registry is process-local to the ASGI application instance. Multi-worker cancellation across independent API processes requires a durable cancellation flag in the generation-lease store and is intentionally not claimed in this phase.

## Tests

Tests cover cancellation before model invocation, cancellation after a streamed delta, iterator closure, no subsequent tool execution, authenticated cancellation, owner isolation, and persistence of the cancelled turn.
