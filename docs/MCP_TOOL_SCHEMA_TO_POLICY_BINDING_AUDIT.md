# MCP Tool Schema-to-Policy Binding Audit

## Finding

**Severity: Medium — execution authorization is not bound to the discovered tool input schema**

The runtime authorizes a tool invocation using owner/provider/account, tool name, host-configured risk and scopes, policy revision, and owner approval. It does not bind that authorization to the remote tool's discovered input schema, nor validate invocation arguments against that schema.

A remote MCP server can change the schema (or the meaning/shape of accepted arguments) for an already-approved tool name without changing the host policy identity or policy revision. The same name remains allow-listed and can be dispatched with arguments that do not conform to the schema the user or operator previously reviewed.

This is a schema-drift and informed-approval gap. It is not evidence that an untrusted remote server can create host policy, bypass owner/provider/account scoping, or elevate the host-configured risk classification.

## Evidence

### Discovery is separate from authorization

- `core/mcp_tool_discovery.py` obtains remote tool names, descriptions, and `input_schema`, bounds and freezes the schema, and returns an inert discovery snapshot.
- `MCPToolDiscoverySnapshot` is not persisted into the policy store or supplied to the policy registry.
- `core/mcp_runtime.py` defines trusted tool profiles using only `tool_name`, host-selected `risk`, and `required_scopes`. There is no schema digest, schema version, or schema document in `MCPTrustedToolDefinition`.

### Approval and dispatch do not validate against the discovered schema

- `core/mcp_tool_policy.py` creates `MCPToolApprovalRequest` with owner/provider/account, tool name, risk, policy version, and arguments. It contains no discovered schema identity.
- `MCPToolPolicyRegistry.build_approver()` validates that arguments are a JSON object and no larger than 64 KB, checks policy/scopes/revision, and obtains owner confirmation for write/financial tools. It does not perform JSON Schema validation.
- `core/mcp_client.py` checks the name against `allowed_tools`, validates JSON serialization and size, calls the approver, and sends the arguments to `tools/call`. It does not consult a schema snapshot or schema digest.
- The policy revision changes when the host policy is mutated; remote schema changes do not advance that revision.

### Test coverage

The inspected tests cover inert discovery, schema size/depth and immutability, host-owned risk classification, scope checks, policy revision fencing, and argument JSON/size checks. They do not cover schema changes between discovery and execution or arguments that violate a previously discovered schema.

## Impact and boundaries

If a server changes the input schema or its semantics while retaining the same tool name, the host's existing name-based policy remains applicable. A call may therefore be approved and dispatched even though its argument contract differs from the one shown during discovery or approval preparation.

This can cause incorrect or unintended remote operations, especially where a tool's parameter meaning changes while its name and host-configured risk profile remain unchanged. The host-configured risk and scope profile still constrain which tool name is callable, and consequential calls still require owner confirmation; those controls do not establish that the approved argument contract matches the current remote contract.

The issue assumes the configured remote MCP endpoint can change its advertised schema or behavior. This review does not establish endpoint compromise or prove that any specific server has performed such a change.

## Recommended remediation

1. Canonicalize each discovered input schema and compute a stable cryptographic digest.
2. Bind the reviewed schema digest (and, where appropriate, server identity/version) to the host-reviewed tool profile and owner approval request.
3. Before dispatch, ensure the currently accepted schema identity matches the approved digest. On drift, fail closed and require rediscovery/review/reapproval.
4. Validate arguments against the bound schema using a vetted JSON Schema implementation with strict resource limits and a deliberately selected supported draft.
5. Do not treat a schema digest as a substitute for host-owned risk classification, OAuth scopes, allowlists, or per-call confirmation.
6. Add deterministic tests where the same tool name is returned with a changed schema between discovery and execution, and where arguments violate the bound schema. Verify that no remote `tools/call` request is sent.

If runtime schema re-fetch is too expensive or unreliable, use an immutable, explicitly approved schema snapshot and invalidate it on provider/session generation changes.

## Scope and verification limits

Reviewed at PR #1346 head `e03d2ec54b5d3037d68877d17112b7b6f1e158ea`:

- `core/mcp_tool_discovery.py`
- `core/mcp_tool_policy.py`
- `core/mcp_tool_policy_store.py`
- `core/mcp_client.py`
- `core/oauth_mcp_connection.py`
- `core/mcp_runtime.py`
- `api/mcp_management.py`
- `tests/test_mcp_tool_discovery.py`
- `tests/test_mcp_tool_policy.py`
- `tests/test_oauth_mcp_connection.py`

Source inspection only. No runtime tests, live server schema mutation, or end-to-end model approval flow was exercised.
