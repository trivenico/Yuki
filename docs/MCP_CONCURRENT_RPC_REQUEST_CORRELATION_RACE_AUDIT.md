# MCP Concurrent RPC Request Correlation Race Audit

## Finding

**Severity: Medium — valid concurrent RPC responses can be rejected**

`MCPHTTPClient._rpc()` assigns monotonically increasing JSON-RPC IDs using the shared instance field `self._request_id`, then later validates the response against that same mutable field. It does not retain the request's assigned ID in a local variable.

When two RPC calls overlap on the same client, the later call can increment the shared counter before the earlier response is validated. The earlier response then contains its correct ID but is compared against the newer request ID and rejected as a mismatched response.

This is a deterministic request-correlation race under overlapping requests, independent of the remote server returning malformed data.

## Evidence

In `core/mcp_client.py`, `_rpc()` currently follows this sequence:

```python
self._request_id += 1
...
if not notification:
    payload["id"] = self._request_id
...
status, response_headers, body = self._http.post_json(...)
...
if not notification and result.get("id") != self._request_id:
    raise MCPError("MCP server returned a mismatched response identifier.")
```

The transport call is synchronous within each invocation, but the client has no lock preventing separate caller threads/tasks from entering `_rpc()` concurrently. The transport's network I/O can overlap when the client is shared across worker threads or concurrent synchronous execution contexts.

Example interleaving:

1. Call A increments counter to 1 and sends JSON-RPC ID 1.
2. Call A blocks waiting for its network response.
3. Call B increments counter to 2 and sends JSON-RPC ID 2.
4. Response A arrives with the correct ID 1.
5. Call A compares ID 1 to the shared current counter 2 and rejects it.
6. Call B may validate successfully against ID 2.

The check is therefore not correlated to the request that is being completed.

## Additional shared session state

The same method updates `self.session_id` from every response, while other concurrent requests may already have copied an earlier session ID into their headers. This can create ordering-dependent session-header behavior if the server changes or first establishes a session while calls overlap.

Initialization is also represented by a shared boolean. Concurrent calls to `initialize()` can both observe an uninitialized client and send duplicate initialization requests. The implementation does not serialize initialization and the initialized notification as one lifecycle operation.

These are secondary concurrency concerns; the request-ID comparison is the directly demonstrated defect.

## Impact

- Concurrent discovery/listing/tool calls on one shared client can fail despite valid JSON-RPC responses.
- Higher-level retries may duplicate requests or create ambiguous tool outcomes, depending on which caller retries and whether the remote operation is idempotent.
- Concurrent session establishment or session-ID rotation can result in requests using stale session headers.
- The repository's client API does not document or enforce single-thread ownership, so callers can share the object without receiving a clear concurrency rejection.

No evidence establishes that the current runtime necessarily shares each client across threads. The defect exists in the client implementation whenever overlapping calls occur.

## Recommended remediation

1. Capture a request-local identifier before transport:
   - increment/allocate the ID under a lock;
   - assign it to a local `request_id`;
   - place that exact value in the payload;
   - compare the response ID against the local value, never the shared counter.
2. Decide and document the supported concurrency model:
   - either serialize all RPCs/session mutations with a per-client lock, or
   - support concurrent RPCs with request-local correlation and explicit synchronization for session state.
3. Serialize `initialize()` and the initialized notification so only one initialization lifecycle can be in progress.
4. If concurrent requests are supported, define how session ID updates are ordered and how requests behave when the server first establishes or rotates a session.
5. Add deterministic barrier-based regression tests:
   - hold request A in the transport after it has sent ID 1;
   - send request B with ID 2;
   - release response A first with ID 1;
   - assert A succeeds and B succeeds when its response arrives;
   - repeat with reverse response order;
   - test concurrent initialization and session-ID establishment.
6. Ensure retry/unknown-outcome handling does not automatically replay non-idempotent tool calls merely because a local response-correlation race occurred.

## Scope and verification limits

Reviewed:

- `core/mcp_client.py`
- `core/mcp_pinned_transport.py`
- `core/oauth_mcp_connection.py`
- `core/mcp_tool_discovery.py`
- `tests/test_mcp_pinned_transport.py`
- `tests/test_oauth_mcp_connection.py`

The pinned transport correctly correlates SSE events to the ID in each individual payload. The defect is in the higher-level MCP client, which compares the decoded response against a mutable shared counter.

No runtime tests were executed during this source review. No live MCP provider behavior is asserted.
