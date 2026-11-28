# MCP JSON-RPC Error Outcome Classification Audit

## Finding

**Severity: Medium — a protocol error response is treated as proof of failed execution**

In `core/mcp_client.py`, `MCPHTTPClient.call_tool()` catches `MCPRemoteError` separately and records `execution_failed`. The unresolved-execution query treats `execution_failed` as a terminal event, so the invocation is removed from reconciliation results.

However, an explicit JSON-RPC error response only establishes that the server returned an RPC error. It does not establish that a remote tool caused no side effects before the error was generated. A remote handler can perform a partial or complete external action and then fail while constructing or returning its response, or an intermediary/server layer can report an error after the action has already been accepted.

The current event therefore asserts a stronger outcome than the client can independently know.

## Source evidence

- `core/mcp_client.py`, `_rpc()`: any JSON-RPC response containing an `error` member raises `MCPRemoteError`.
- `MCPHTTPClient.call_tool()`: the dedicated `except MCPRemoteError` branch records `execution_failed`.
- `core/approval_audit.py`, `list_unresolved_executions()`: `execution_failed` is included in the terminal-event set, alongside `executed`, `execution_blocked`, and reconciliation outcomes.
- `docs/PHASE196_MCP_EXECUTION_AUDIT.md`: documents explicit JSON-RPC errors as failures and says unknown outcomes are for transport/protocol ambiguity. That distinction does not account for side effects that precede an explicit server error.

This is distinct from the existing audit-correlation finding: it concerns whether a terminal execution outcome is justified, not which approval event belongs to an invocation.

## Impact and boundaries

For a write or financial tool, an operation may have changed remote state even though Yuki records `execution_failed` and no longer lists it as unresolved. An operator may then retry an operation that actually took effect or partially took effect, causing duplicate or inconsistent remote actions.

This does **not** show that an MCP server actually performed a side effect before returning an error, nor does it bypass owner approval. The risk is that the client lacks evidence to safely classify every explicit RPC error as non-execution.

The client already avoids automatic retries, which limits immediate duplicate execution. The concern is the durable outcome classification and the recovery workflow that consumes it.

## Recommended remediation

1. Treat an explicit JSON-RPC error from a consequential `tools/call` as `outcome_unknown` unless the server provides a documented, trusted guarantee that the specific error class proves no action was performed.
2. Preserve the sanitized RPC error code/class as bounded metadata if useful, without persisting raw remote messages or sensitive data.
3. Distinguish a confirmed tool-level error from a confirmed non-execution result only where the provider/tool contract explicitly guarantees that semantic.
4. Keep ambiguous outcomes visible to owner-authenticated reconciliation; never convert `outcome_unknown` into permission to retry automatically.
5. Add tests for JSON-RPC errors on write/financial calls asserting that the event remains unresolved, and tests for any explicitly trusted non-execution error contract if one is introduced.
6. For operations where duplicate execution is unacceptable, use server-enforced idempotency keys or a provider-specific operation-status lookup before retry.

## Review limitations

Source-only review at PR #1346 head `043dc137d4b889d9ec541d54272fa83833bfb06a`. No live MCP server, remote side-effect simulation, tests, or CI were executed. This is a classification/recovery correctness finding, not a demonstrated production incident.
