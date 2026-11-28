# MCP Server Session Expiry and Reinitialization Recovery Audit

## Finding

**Severity: Medium — invalidated MCP sessions are not detected or recovered**

The Streamable HTTP transport rejects all non-success HTTP statuses through one generic transport error. It does not preserve HTTP status or response headers for the MCP client to distinguish an expired/unknown session (HTTP 404) from other failures.

When a server invalidates a previously issued MCP session identifier, the client therefore cannot recognize the session-expiry condition, clear its stale session ID, or transition back through initialization. The same client object retains the invalid session ID and continues attaching it to later requests.

## Evidence

### 1. Transport collapses HTTP status codes

In `core/mcp_pinned_transport.py`, `PinnedMCPHTTPTransport.post_json()` accepts only statuses 200, 202, and 204. Every other status raises `MCPPinnedTransportError("mcp_http_request_failed")`.

The exception does not preserve the HTTP status or response headers. This means the caller cannot distinguish HTTP 404 (unknown/expired session) from 401, 403, 429, or 5xx responses.

### 2. Client maps transport failure to a generic error

In `core/mcp_client.py`, `MCPHTTPClient._rpc()` catches `MCPPinnedTransportError` and raises the same sanitized `MCPError("MCP server was unreachable or rejected the request.")` for every transport failure.

It does not receive the HTTP status, identify an invalid session, clear `self.session_id`, or expose a session-recovery state transition.

### 3. Stale session identifier is retained

The client stores the returned session ID in `self.session_id`. When a later request using that ID receives HTTP 404, the exception path exits before any session reset. Later requests continue adding the same `Mcp-Session-Id` header.

There is no public reinitialize/reset operation that safely transitions an existing client from an established session to a new session.

### 4. Existing tests do not cover session invalidation

The inspected `tests/test_mcp_client_transport.py` covers session propagation after initialization and generic transport failure sanitization. The inspected pinned-transport tests cover redirects, destination validation, response bounds, and SSE behavior. They do not exercise a server returning HTTP 404 for a previously established session, nor verify that the client can establish a replacement session.

## Impact

A server-side session expiry, restart, or explicit session termination can leave the local client permanently holding an unusable session identifier. Calls then fail repeatedly until the caller discards and recreates the client through an external lifecycle path.

This is primarily an availability and lifecycle-correctness defect. It does not by itself establish an authorization bypass or credential disclosure.

A recovery implementation must be careful with non-idempotent tool calls: it must not blindly replay a request whose outcome is ambiguous. Session reinitialization and replay of the original operation are separate decisions.

## Recommended remediation

1. Preserve a bounded, typed HTTP status in transport errors without exposing response bodies, credentials, or sensitive headers.
2. Distinguish an HTTP 404 associated with a request carrying an MCP session ID from generic transport failures.
3. On invalid-session detection, invalidate the local session ID and transition the client to an explicit uninitialized state.
4. Reinitialize through the normal protocol handshake before any subsequent session-bound operation.
5. Do not automatically replay a tool call after session loss. The server may have processed the operation before the connection/session failure became visible; require an explicit idempotency strategy or caller reconciliation before retrying.
6. Serialize session invalidation and reinitialization with ordinary RPCs. Coordinate with the concurrent RPC state-race remediation so an old in-flight response cannot overwrite a newly established session ID.
7. Add tests for:
   - 404 after a successful initialization;
   - stale session ID removal;
   - a fresh initialization handshake and replacement session ID;
   - subsequent requests using only the replacement session;
   - 401/403/429/5xx remaining distinguishable and not triggering inappropriate reinitialization;
   - concurrent old-session response and reinitialization ordering;
   - no automatic replay of a non-idempotent tool call after session loss.

## Scope and verification limits

Reviewed at PR #1346 head `b0aed2b7accdb2bd8eb707181bb1852626791111`:

- `core/mcp_client.py`
- `core/mcp_pinned_transport.py`
- `core/oauth_mcp_connection.py`
- `core/mcp_runtime.py`
- `tests/test_mcp_client_transport.py`
- `tests/test_mcp_client_auth.py`
- `tests/test_mcp_pinned_transport.py`
- `docs/MCP_CLIENT_SESSION_DISCONNECT_RECONNECT_LIFECYCLE_AUDIT.md`

Source inspection only. No runtime tests, live MCP server session expiry, or fault-injection reproduction were executed.

## Related existing audits

- `docs/MCP_CLIENT_SESSION_DISCONNECT_RECONNECT_LIFECYCLE_AUDIT.md` concerns stale session reuse across OAuth credential lifetimes.
- `docs/MCP_CLIENT_CONCURRENT_RPC_STATE_RACE_AUDIT.md` concerns overlapping RPCs racing request correlation and session state.
- `docs/MCP_PROTOCOL_VERSION_NEGOTIATION_AUDIT.md` concerns validating and applying the server-selected protocol version.

This report covers server-side session invalidation within a single credential lifetime and is distinct from the above findings.