# MCP JSON-RPC Response Envelope Validation Audit

## Finding

**Severity: Medium — malformed responses can be accepted and tool outcomes misclassified**

The JSON response path in `MCPHTTPClient._rpc()` checks that the decoded value is a dictionary and that `jsonrpc == "2.0"`, then compares the response ID with ordinary Python equality. It does not strictly validate the response ID type or enforce the JSON-RPC response envelope's mutually exclusive result/error shape.

This creates multiple concrete acceptance gaps:

1. Python considers `True == 1` and `1.0 == 1`. A response containing `"id": true` or `"id": 1.0` can therefore pass correlation for a request whose ID is integer `1`, despite not matching the request ID's JSON type.
2. A response with neither `result` nor `error` passes the envelope checks and returns `None`. For a tool call, the client can then record `executed` despite receiving no JSON-RPC result or error.
3. A response containing both `result` and `error` is accepted as an error because the implementation only checks whether the `error` key exists.
4. The `error` value is not validated as a JSON-RPC error object. Values such as `null`, strings, or arbitrary objects are treated as explicit remote errors.

## Evidence

In `core/mcp_client.py`, `_rpc()` performs:

```python
if not isinstance(result, dict) or result.get("jsonrpc") != "2.0":
    raise MCPError("MCP server returned an invalid JSON-RPC envelope.")
if not notification and result.get("id") != self._request_id:
    raise MCPError("MCP server returned a mismatched response identifier.")
if "error" in result:
    raise MCPRemoteError("MCP server returned a JSON-RPC error.")
return result.get("result")
```

There is no exact type check for the response ID, no required-key check for exactly one of `result` or `error`, and no validation of the error object's required fields.

The SSE transport path already compares the response ID using exact type equality as well as value equality. The JSON path does not, creating inconsistent validation between the two supported response formats.

## Impact

- A malformed or non-conforming response can pass correlation when Python's cross-type equality considers its ID equal to the integer request ID.
- A response missing both result and error can be treated as a successful `None` result. In `call_tool()`, this leads to the normal `executed` audit event because the result is not an `isError` object.
- Malformed error payloads are classified as explicit remote failures. `call_tool()` records `execution_failed` for `MCPRemoteError`, whereas other response/protocol exceptions are recorded as `outcome_unknown`. Treating an invalid response as a definitive remote error can therefore misstate whether the remote tool operation executed.

This is a protocol-integrity and execution-accounting issue. The inspected source does not establish a direct authorization bypass.

## Recommended remediation

1. Compare response IDs with both exact type and value:
   - require `type(response_id) is type(request_id)`;
   - then compare values.
2. Require exactly one of `result` and `error` for a response. Reject both-present and neither-present envelopes.
3. Validate JSON-RPC error objects: require an object with an integer `code` (excluding booleans) and string `message`; permit `data` only as an optional field.
4. Preserve protocol-invalid responses as an outcome-unknown execution result for tool calls, not as a definitive remote execution failure.
5. Keep notification handling distinct: notifications have no response ID and should not be subjected to request-response envelope validation if the transport contract permits a body for a notification.
6. Add regression tests for:
   - integer request ID versus boolean and float response IDs;
   - missing both `result` and `error`;
   - both `result` and `error`;
   - null, string, and malformed error objects;
   - a malformed tool-call response producing `outcome_unknown`, not `execution_failed`;
   - equivalent valid and invalid cases for JSON and SSE responses.

## Scope and verification limits

Reviewed:

- `core/mcp_client.py`
- `core/mcp_pinned_transport.py`
- `core/mcp_tool_discovery.py`
- `tests/test_mcp_pinned_transport.py`

The SSE transport checks exact response-ID type and value. The JSON client validation does not. No runtime tests were executed during this source review, and no live MCP provider behavior is asserted.
