# Phase 151 — Streaming conversation responses

## HTTP contract

Authenticated clients may submit a turn to:

`POST /v1/conversations/{conversation_id}/messages/stream`

The request body is identical to the ordinary message endpoint:

```json
{
  "content": "Hello Yuki",
  "request_id": "client-generated-id",
  "expected_version": 0
}
```

The response uses Server-Sent Events (SSE), with `text/event-stream` content type:

- `event: delta` — JSON data containing one validated text fragment.
- `event: completed` — JSON data containing the persisted conversation result and new version.
- `event: error` — generic error code; never provider internals or credentials.

The regular non-streaming endpoint remains supported.

## Provider adapter contract

`AgentExecutionLoop` accepts an optional `stream_model` callable. It yields mappings:

- `{"type": "text_delta", "text": "..."}`
- `{"type": "response", "response": {"text": "...", "tool_calls": {"tool_calls": [...]}}}`

Text fragments are bounded and validated. A final response event carries structured tool calls through the existing parser and guarded tool runtime. Streaming never creates a second tool execution path. If no stream adapter is configured, the service still returns a completed SSE event with the final answer; no incremental deltas are claimed.

## Persistence and execution

The authenticated ASGI route runs synchronous generation in a worker thread so it does not block the event loop. The conversation service persists the user turn before model execution and persists the assistant result before emitting the completed event. Existing generation leases, owner scoping, request idempotency and version checks remain in force.

## Limitations / production follow-ups

- Provider-specific streaming adapters must be implemented and configured for each supported model vendor; this phase provides the normalized interface and transport.
- Client disconnect currently does not cancel an in-flight provider request. Add a durable cancellation token and provider-specific cancellation in a later phase.
- Configure reverse proxies to disable buffering and enforce idle/read timeouts. The response sets `X-Accel-Buffering: no`.
- Use TLS, origin policy and rate limiting at the deployment boundary.
- Streaming tool-call deltas are not forwarded to the browser. Tool calls remain server-side; only user-visible text deltas are emitted.
