# MCP Tool Argument Approval-to-Dispatch Integrity Audit

## Finding

**Severity: Medium — the argument object validated and approved is not an immutable invocation snapshot**

`MCPHTTPClient.call_tool()` accepts a caller-owned mutable dictionary. It serializes that dictionary once to check JSON serializability and the 64 KB limit, then passes the original object to the approver. After approval and the `execution_started` audit append, it passes that same original object to `_rpc()`, which serializes it for the HTTP request.

There is no immutable or detached snapshot spanning validation, approval, audit, and dispatch. A concurrent caller thread can mutate the dictionary (including nested dictionaries/lists) after validation or approval but before request serialization. The dispatched arguments can therefore differ from those validated and shown to the approval callback.

The approver is also handed the same mutable object held by the caller and later used for dispatch. Although the built-in policy registry constructs a separate deeply immutable copy for its owner-confirmation request, `MCPHTTPClient` accepts any injected approver callback; a callback that mutates its input can alter the eventual dispatched payload after the initial validation.

## Evidence

In `core/mcp_client.py`, `MCPHTTPClient.call_tool()`:

1. Checks that `arguments` is a dictionary.
2. Calls `json.dumps(arguments, ...)` to validate serializability and enforce the 64 KB limit.
3. Calls `self.approver(name, arguments)` with the original dictionary.
4. Writes the `execution_started` audit event.
5. Calls `self._rpc("tools/call", {"name": name, "arguments": arguments})` with that same dictionary.

The JSON-RPC payload is serialized later in the HTTP transport, so the first serialization is not the payload that is guaranteed to be sent.

In `core/mcp_tool_policy.py`, the built-in approver serializes and deep-freezes a copy for `MCPToolApprovalRequest.arguments`. This protects the approval request object, but does not freeze or replace the original dictionary retained by `MCPHTTPClient`.

The inspected tests verify JSON validity, size limits, and immutability of the approval request copy. They do not verify that the exact approved argument snapshot is the one passed to the transport when the caller-owned dictionary changes during approval or audit.

## Impact and boundaries

For a consequential operation, the user/owner may approve one argument set while the MCP server receives a different set. Depending on the tool semantics, this can cause an unintended remote action despite a successful approval callback.

This is a TOCTOU integrity issue. It does not bypass the host allowlist, owner/provider/account scoping, OAuth scope checks, or trusted risk classification. Exploitation requires mutation through a shared reference or a concurrent caller; this review does not establish a remote attacker can directly mutate the in-process object.

## Recommended remediation

1. At method entry, create a detached, canonical JSON snapshot once, using strict JSON serialization (`allow_nan=False`) and the existing byte limit.
2. Pass a separate detached copy of that snapshot to the approver so an injected callback cannot mutate the dispatch copy.
3. Ensure the exact canonical snapshot is used to construct the outbound `tools/call` payload; do not re-read the caller-owned dictionary after approval.
4. Consider representing the invocation internally as immutable canonical bytes plus a decoded read-only approval view. Preserve JSON semantics and reject duplicate-key or non-finite values where applicable.
5. Add deterministic tests where:
   - another thread mutates the original nested argument dictionary while the approver is blocked;
   - the approver deliberately mutates its received dictionary before returning literal `True`;
   - the execution audit callback mutates the caller-owned dictionary;
   - the fake transport asserts that dispatched arguments exactly match the snapshot presented for approval.
6. Verify every mutation scenario either dispatches the exact approved snapshot or fails closed before any remote `tools/call` request.

## Replay and ambiguous outcomes

The client generates a fresh JSON-RPC request ID for each request and records `outcome_unknown` when a non-remote exception occurs after dispatch may have begun. It does not blindly retry the request after an ambiguous transport outcome, which is a useful duplicate-execution safeguard.

However, a JSON-RPC request ID is a response-correlation identifier, not a durable idempotency key. The reviewed MCP call path does not establish end-to-end deduplication if an upstream caller manually retries a logically identical operation after an ambiguous result. Consequential remote tools should support a host-generated idempotency key in their application-level arguments or another server-enforced deduplication mechanism where duplicate execution is unacceptable. This is a limitation, not a separately confirmed exploitable replay vulnerability in the reviewed code.

## Scope and verification limits

Reviewed at PR #1346 head `0432b4c932051b9977369e2b9d06a45519bab603`:

- `core/mcp_client.py`
- `core/mcp_tool_policy.py`
- `core/mcp_tool_policy_store.py`
- `core/mcp_runtime.py`
- `tests/test_mcp_client_auth.py`
- `tests/test_mcp_client_transport.py`
- `tests/test_mcp_tool_policy.py`
- `tests/test_mcp_tool_policy_store.py`

Source inspection only. No runtime race tests, concurrent stress tests, or live MCP server calls were executed.
