# MCP Client Concurrent RPC State Race Audit

## Finding

**Severity: Medium — concurrent RPCs race request correlation and MCP session state**

`MCPHTTPClient` stores the next JSON-RPC request identifier and the current MCP session identifier on the client instance. Its `_rpc()` method performs a network request without synchronization, then validates the response against the mutable instance request counter and updates the shared session identifier.

When two threads/tasks use the same client concurrently, these operations are not bound to an individual request. A response can be compared against a request ID assigned by a later call, and each request can capture or overwrite session state at different points in its lifecycle.

## Evidence

In `core/mcp_client.py`:

1. `_rpc()` increments `self._request_id`.
2. It copies the current value into the outgoing payload's `id`.
3. It reads `self.session_id` to construct the request headers.
4. It performs the network call.
5. It updates `self.session_id` from the response headers.
6. It validates the response using `result.get("id") != self._request_id`, reading the mutable instance field again rather than the request ID assigned to this specific request.

No lock, per-request local correlation variable, or other concurrency control protects these operations.

## Example interleaving

- Call A increments the counter and sends request ID 1.
- Call B increments the counter and sends request ID 2.
- Call A receives its valid response with ID 1.
- Call A compares ID 1 to the now-current shared counter value 2 and rejects the response as mismatched.

The shared `session_id` has a related race: concurrent requests can both read the same previous session value and then overwrite it based on response headers in completion order. A server that establishes or rotates session state can therefore leave the client with a session identifier that is not consistent with the request sequence.

## Impact and boundaries

Concurrent calls through a shared client can fail despite valid server responses. In session-establishing or session-changing deployments, races can also cause later calls to use inconsistent session context.

The finding does not establish remote authorization bypass or cross-account credential disclosure. The precise remote effect depends on server session semantics. It is a local client correctness and availability defect at a security-sensitive protocol boundary.

The existing MCP session disconnect/reconnect audit covers reuse of a client across OAuth credential lifetimes. This report is distinct: it concerns concurrent calls made during a single client lifetime.

## Recommended remediation

1. Capture the request identifier in a local immutable variable and validate the response against that exact value; never compare against the mutable counter after network I/O.
2. Protect request-ID allocation with a lock or use a thread-safe monotonic counter.
3. Define an explicit concurrency model for session state. The safest initial implementation is to serialize stateful MCP RPCs per client with a lock covering session-header snapshot, request, response validation, and session update.
4. If concurrent requests are required, use a protocol-aware session coordinator that ensures session initialization and session-ID changes are ordered and that every request uses a consistent session snapshot.
5. Do not allow notifications or initialization transitions to race with ordinary RPCs; explicitly serialize initialization and lifecycle transitions.
6. Add deterministic barrier-based tests that force overlapping RPCs, reverse response order, return distinct session IDs, and assert correct request correlation and session propagation.

## Scope and verification limits

Reviewed at PR #1346 head `e303637311925d97934f10abd51d5a131c6d499c`:

- `core/mcp_client.py`
- `core/mcp_pinned_transport.py`
- `core/mcp_runtime.py`
- `core/oauth_mcp_connection.py`
- `tests/test_mcp_client_transport.py`
- `tests/test_mcp_client_auth.py`
- `tests/test_mcp_pinned_transport.py`

The existing tests cover sequential request correlation, session propagation, response bounds, DNS pinning, redirects, and SSE handling. The inspected client tests do not exercise overlapping RPCs.

Source inspection only. No runtime tests or concurrent reproduction were executed.