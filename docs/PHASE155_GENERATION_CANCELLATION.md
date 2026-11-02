# Phase 155 — Conversation generation cancellation

## User-facing behavior

An authenticated client can cancel a currently active streaming generation:

```http
POST /v1/conversations/{conversation_id}/messages/cancel
Authorization: Bearer <session-token>
Content-Type: application/json

{"request_id":"the-original-request-id"}
```

The response is `{"cancel_requested": true}` when that exact user's generation is active, otherwise `false`. Conversation ownership is checked before looking up the active request, so guessed IDs cannot cancel another user's work.

## Disconnect handling

The streaming ASGI endpoint watches for `http.disconnect`. A disconnected client sets the same cancellation signal as the explicit cancel endpoint. The active request registry is keyed by authenticated owner, conversation and idempotency request ID, and is removed when the worker finishes.

## Agent-loop behavior

`AgentExecutionLoop.run` accepts an optional per-run `cancellation_requested` callback. It checks this callback before model turns, between provider stream events, and before guarded tool dispatch. When cancellation is observed during streaming, the stream iterator is closed when it supports `close()`, no further deltas are forwarded, and the execution returns status `cancelled`.

The conversation service persists a sanitized assistant turn stating that generation was stopped at the user's request. The generation lease is released in the normal `finally` path.

## Safety and limitations

- Cancellation is scoped to the authenticated owner, conversation and request ID.
- No user-supplied tool schema or owner identifier is accepted by the cancel endpoint.
- No unguarded tool execution is introduced.
- Provider generator cancellation is cooperative. If a provider is blocked inside a synchronous network read, cancellation is observed after that read returns or times out. Hard interruption of in-flight socket reads and provider-specific abort support remain follow-up work.
- A disconnected client does not imply that an upstream provider has instantly stopped billing or generation.

## Validation

Tests cover per-run cancellation between stream events, authenticated cancellation, cross-owner isolation, and persistence of the cancelled status. CI must pass on the exact PR head before merge.
