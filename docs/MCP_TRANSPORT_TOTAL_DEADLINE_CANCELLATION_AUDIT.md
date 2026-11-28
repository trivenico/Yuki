# MCP Transport Total Deadline and Cancellation Audit

## Finding

**Severity: Medium — socket inactivity timeout does not bound total MCP request duration**

The pinned transport configures a socket timeout (1–30 seconds), but it does not enforce a monotonic end-to-end deadline for the complete DNS, connect, request, and response operation. The timeout is applied to blocking socket operations, not to the total elapsed lifetime of a request.

In particular, the SSE response reader repeatedly calls `read1(4096)` until it receives the matching JSON-RPC response. It accepts keepalive comments and unrelated/progress events and continues reading. A peer that sends small chunks often enough to avoid an individual socket-read timeout can keep a synchronous worker occupied for an extended period, until the response byte limit is reached or the matching response arrives.

The ordinary JSON response path also performs a bounded-size `response.read()` without a total-duration deadline; a slow-drip response can extend the request beyond the configured socket timeout.

The client/transport API has no cancellation token or close/cancel operation that an upstream caller can use to interrupt an in-flight request. The transport closes its connection in `finally`, but only after the request path returns or raises.

## Evidence

In `core/mcp_pinned_transport.py`:

1. `PinnedMCPHTTPTransport.__init__()` validates and stores `timeout` as the socket timeout passed to the connection factory.
2. `post_json()` performs DNS resolution, connection, request, and response reading in one synchronous call. There is no monotonic deadline computed at entry and no remaining-time budget passed between stages.
3. `_read_sse_response()` loops until the matching JSON-RPC response arrives or the stream ends/errors. It bounds line size, event size, and total response bytes, but not total elapsed time or idle periods at the application level.
4. The JSON response path calls `response.read(MAX_RESPONSE_BYTES + 1)`, with no total deadline or cancellation check.
5. The connection is closed in `finally`, after the read path exits.

In `core/mcp_client.py`, `_rpc()` and `call_tool()` are synchronous and expose no cancellation/deadline parameter. The runtime forwards a timeout value to client construction, but that value is only the transport socket timeout.

## Impact and boundaries

A malicious, compromised, or simply malfunctioning MCP endpoint can consume application worker/thread capacity for much longer than the configured timeout by slowly streaming data or SSE keepalives/progress events. Under concurrency, enough slow requests can exhaust the worker pool and degrade MCP-backed features or other work sharing those workers.

The transport already bounds request and response sizes, individual socket operations, and destination addresses. Those controls reduce other risks but do not impose a wall-clock request budget. The existing phase documentation also notes that DNS resolution is not independently time-bounded; this report additionally covers the total response/read lifetime after resolution.

No remote code execution, credential disclosure, or authorization bypass is established. The impact is availability/resource exhaustion.

## Cancellation behavior

There is no supported way to cancel an in-flight synchronous MCP RPC from the client/runtime API. A caller abandoning its wait does not itself guarantee that the underlying socket operation or remote tool execution stops. Closing a local connection can interrupt local I/O when the transport is designed to expose and coordinate that operation, but it cannot guarantee that the remote server has stopped executing a tool after receiving the request.

For consequential tool calls, cancellation must therefore be modeled as an outcome that may be unknown, not as proof that remote execution was rolled back.

## Recommended remediation

1. Compute a monotonic absolute deadline at the beginning of each transport request.
2. Enforce that same deadline across DNS resolution, connect, request write, response headers, and every response read. Pass only the remaining time to each blocking operation.
3. Bound SSE total duration and idle duration independently; terminate streams that exceed either budget, including streams containing only keepalives or unrelated progress events.
4. Add a cooperative cancellation handle/token to the transport and client. Ensure cancellation closes the active connection and prevents subsequent response processing.
5. Propagate request deadlines/cancellation through runtime call sites instead of exposing only a socket timeout.
6. Record a distinct sanitized cancelled/timeout/unknown-outcome lifecycle event. Do not automatically retry non-idempotent tool calls after cancellation or timeout once dispatch may have begun.
7. Document that local cancellation cannot guarantee remote tool cancellation. For long-running operations, use protocol-level task handles/cancellation if supported by the provider and reconcile final remote state.
8. Add deterministic tests using a fake clock and chunked response:
   - SSE keepalives arriving just before each socket timeout must still hit the total deadline.
   - A slow-drip JSON response must hit the same deadline.
   - Cancellation during an active read closes the connection and returns promptly.
   - Cancellation after dispatch records an ambiguous outcome and does not replay the tool call.
   - DNS/connect/read phases consume one shared deadline rather than resetting it.

## Related existing audits

- `docs/PHASE178_MCP_PINNED_HTTP_TRANSPORT.md` notes that DNS resolution is not independently time-bounded.
- `docs/MCP_CLIENT_SESSION_EXPIRY_REINITIALIZATION_AUDIT.md` covers invalid remote sessions and recovery.
- `docs/MCP_CLIENT_CONCURRENT_RPC_STATE_RACE_AUDIT.md` covers concurrent request/session state races.
- `docs/MCP_TOOL_ARGUMENT_APPROVAL_DISPATCH_INTEGRITY_AUDIT.md` covers argument snapshot integrity and the `outcome_unknown` state for ambiguous dispatches.

This report addresses the distinct absence of a total request deadline and cooperative in-flight cancellation.

## Scope and verification limits

Reviewed at PR #1346 head `d563554e82699665cb9c0e0b8d1e965aff92c736`:

- `core/mcp_pinned_transport.py`
- `core/mcp_client.py`
- `core/mcp_runtime.py`
- `core/oauth_mcp_connection.py`
- `tests/test_mcp_pinned_transport.py`
- `tests/test_mcp_client_transport.py`
- `tests/test_oauth_mcp_connection.py`
- `docs/PHASE178_MCP_PINNED_HTTP_TRANSPORT.md`

Source inspection only. No slow-drip server, wall-clock timing test, cancellation test, or live remote tool execution was run.
