# MCP Protocol Version Negotiation Audit

## Finding

**Severity: Medium — server-selected protocol version is neither validated nor applied**

The MCP HTTP client sends a fixed protocol version during initialization, but does not validate the server's selected version or retain it for subsequent requests. It marks the connection initialized and continues sending the hard-coded client version regardless of what the server returned.

This breaks the protocol-version negotiation boundary and can cause the client and server to interpret subsequent messages under different protocol rules.

## Evidence

In `core/mcp_client.py`:

1. `MCPHTTPClient.PROTOCOL_VERSION` is fixed to `2025-03-26`.
2. `initialize()` sends that value in `params.protocolVersion`.
3. The initialization result is only checked to be a dictionary. Its `protocolVersion` member is not required, type-checked, or checked against the versions supported by this client.
4. The returned value is exposed to the caller, with a fallback to the hard-coded version when missing.
5. `_initialized` is set to true.
6. Every subsequent RPC sends `MCP-Protocol-Version: 2025-03-26`, regardless of the server's selected version.

The inspected test `tests/test_mcp_client_transport.py::test_uses_injected_transport_and_propagates_session_id` only tests the case where the response omits `protocolVersion`; it expects the client to silently fall back to its own version. It does not test unsupported or alternate server-selected versions.

## Impact

If a server selects a protocol version other than the one requested, the client currently continues as if negotiation succeeded and advertises a different version on subsequent HTTP requests. This can cause incompatible request/response handling, rejected requests, or inconsistent interpretation of protocol features and lifecycle semantics.

The client currently implements only one declared protocol version. It should therefore fail closed when the server selects a different or malformed version rather than silently proceeding. If multi-version support is introduced later, the negotiated version must be stored per client session and used consistently.

This is a protocol-integrity and interoperability defect. This review does not establish a remote code execution or authorization bypass.

## Recommended remediation

1. Maintain an explicit set of protocol versions supported by the client.
2. Require `initialize` responses to contain a valid, non-empty string `protocolVersion`.
3. Reject the initialization if the selected version is not in the supported set.
4. Store the selected version in an immutable per-session field and use it for every subsequent `MCP-Protocol-Version` header.
5. Do not set `_initialized` or send `notifications/initialized` until negotiation validation succeeds.
6. Add tests for:
   - exact supported-version selection;
   - missing, non-string, empty, and unsupported selected versions;
   - a supported alternate version if multi-version support is added;
   - subsequent requests carrying the exact negotiated version;
   - failed negotiation leaving the client uninitialized and unable to list or call tools.

## Scope and verification limits

Reviewed at PR #1346 head `5d6d60eec73460650e1bdcc8525c9a1bcf989c32`:

- `core/mcp_client.py`
- `core/mcp_pinned_transport.py`
- `core/oauth_mcp_connection.py`
- `core/mcp_runtime.py`
- `tests/test_mcp_client_transport.py`
- `tests/test_mcp_client_auth.py`
- `tests/test_mcp_pinned_transport.py`

Source inspection only. No runtime tests or live MCP server negotiation were executed.

## Related findings

- `docs/MCP_CLIENT_CONCURRENT_RPC_STATE_RACE_AUDIT.md` covers mutable request/session state during concurrent calls.
- `docs/MCP_CLIENT_SESSION_DISCONNECT_RECONNECT_LIFECYCLE_AUDIT.md` covers session reuse across OAuth credential lifetimes.

This report addresses the independent protocol-version negotiation state and does not duplicate those lifecycle findings.