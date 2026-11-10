# Phase 181 — MCP Streamable HTTP SSE responses

## Delivered

- Negotiate both JSON and SSE responses with the MCP endpoint.
- Accept JSON responses and preserve the existing JSON-RPC envelope and response-ID checks.
- Consume `text/event-stream` incrementally using bounded reads.
- Parse SSE comments, event names, data fields, CRLF/LF lines and multi-line data payloads.
- Ignore non-message events and progress notifications while waiting for the matching JSON-RPC response ID.
- Stop reading as soon as the matching response arrives; do not wait for a long-lived stream to close.
- Enforce line, event and total-stream byte limits.
- Keep redirect rejection, DNS pinning, TLS hostname verification, session propagation and bearer-token injection.
- Preserve 202/204 empty notification responses.

## Deliberate boundaries

- This phase returns the correlated terminal JSON-RPC response; it does not expose progress notifications to callers.
- SSE responses to JSON-RPC notifications without an ID are rejected; normal 202/204 notification acknowledgements remain supported.
- The socket read timeout bounds idle waits, but this phase does not implement a separate wall-clock deadline or asynchronous cancellation API.
- The host still controls endpoint allow-lists and tool approval. Server-provided tool descriptions remain untrusted data.
