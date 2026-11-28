# OAuth Disconnect vs In-Flight MCP Dispatch Audit

## Scope

Source review at PR #1346 head `6c603c02ac6bc791eafc2e71e70983e870d4ff3d`. Reviewed:

- `core/oauth_mcp_connection.py`: `OAuthMCPConnectionManager.create_client()` and its lazy access-token provider
- `core/mcp_client.py`: `MCPHTTPClient._rpc()`, `_authorization_header()`, and `call_tool()`
- `core/oauth_disconnect_service.py`: local credential deletion and remote revocation ordering
- `api/oauth_connections.py`: authenticated disconnect API ordering
- `core/oauth_credential_vault.py`: credential lookup and unconditional/conditional disconnect operations
- Relevant OAuth/MCP connection and disconnect tests
- Existing `docs/OAUTH_DISCONNECT_RECONNECT_REGISTRY_RACE_AUDIT.md`

No runtime tests were executed; this is a source-level finding.

## Finding: local OAuth disconnect does not fence an already-authorized MCP request

**Severity: Medium — an MCP tool request can be dispatched after local disconnect has deleted the account credential.**

The OAuth-backed MCP manager constructs a client with an `access_token_provider` closure. For each RPC, `MCPHTTPClient._rpc()` invokes that callback while constructing the Authorization header. The callback refreshes if needed, reads the credential from the vault, validates owner/provider/account and scopes, and returns the access token.

The disconnect path is independent:

1. `AuthenticatedOAuthConnectionAPI` resolves the owner's connection and invokes the provider disconnect service.
2. `OAuthDisconnectService.disconnect()` reads the credential, optionally performs a remote revocation request, and then deletes the local vault row.
3. The API removes the connection registry row and returns success.

There is no shared per-account lifecycle lock, active-dispatch counter, revocation generation, or final credential-generation check between token acquisition and MCP network dispatch. The manager does not register created clients with a revocation coordinator, and the MCP client has no account-disconnect gate.

## Race interleaving

1. MCP tool call passes its allowlist and owner-approval checks.
2. `MCPHTTPClient._rpc()` invokes the token provider.
3. The manager reads a valid access token from the vault and returns it.
4. Disconnect runs concurrently, revokes the selected remote token if configured, deletes the local vault credential, removes the registry entry, and reports success.
5. The original MCP call proceeds with the already-built Authorization header and sends `tools/call` to the remote MCP server.

The local delete prevents later token-provider calls from obtaining the deleted credential, but it cannot retract a token already copied into an in-flight request. The reviewed implementation does not wait for active MCP dispatches to drain or re-check the credential generation immediately before socket transmission.

Remote revocation does not establish a guaranteed cancellation barrier for an MCP request already accepted by the remote server. The disconnect service also prefers the refresh token when one exists; provider-specific access-token revocation semantics and propagation are not established by this source review.

## Impact and boundaries

- A user can receive a successful local disconnect response while a previously authorized MCP tool request is still being transmitted or processed.
- For consequential tools, the remote side effect may occur after the owner believes the integration has been disconnected.
- This is a lifecycle/availability and authorization-revocation timing gap, not evidence that an unauthenticated caller can create a tool call or bypass the existing approval and policy checks.
- Subsequent MCP RPCs that need a new token should fail after the vault row is deleted, assuming no concurrent reconnect creates a replacement credential.
- This finding is distinct from `OAUTH_DISCONNECT_RECONNECT_REGISTRY_RACE_AUDIT.md`, which concerns stale registry removal and stale credential deletion. It is also distinct from the MCP policy-revocation-to-dispatch race: this issue is the OAuth credential lifecycle boundary between token acquisition and transport dispatch.

## Recommended remediation

1. Introduce an owner/provider/account-scoped lifecycle coordinator shared by credential acquisition, MCP dispatch, and disconnect.
2. At minimum, define explicit disconnect semantics: either wait for active dispatches to reach a terminal/unknown state before reporting completion, or revoke a generation immediately and prevent any not-yet-sent dispatch using that generation.
3. Bind token acquisition to a credential generation/version. Before sending the request, validate that the same generation remains active under a synchronization primitive that disconnect also acquires. A check without synchronization still has a check-to-send race.
4. If a dispatch lease is used, hold it across the point at which the request is committed to transport. Disconnect should close the dispatch gate, wait for active leases within a bounded deadline, and report an incomplete/draining state on timeout rather than claiming full revocation.
5. Define behavior for already-sent requests: local socket cancellation cannot prove remote execution stopped. Record `outcome_unknown` when the transport cannot establish whether the server accepted the call; do not automatically retry non-idempotent tools.
6. Ensure the coordinator works across processes if the application can construct OAuth managers or MCP clients in multiple processes. A process-local mutex alone is insufficient.
7. Add deterministic barrier-based tests that pause after token acquisition but before transport send, run disconnect, and verify the chosen contract. Also test disconnect during an active response, concurrent refresh, reconnect to a new credential generation, bounded drain timeout, and cross-process coordination.

## Conclusion

Local credential deletion is correctly attempted even when remote revocation fails, and future token lookups should fail once the row is gone. However, the current architecture has no synchronization between an already-completed token lookup and the subsequent MCP HTTP dispatch. A call can therefore cross the remote boundary after the local disconnect operation reports success. A generation-aware dispatch gate and explicit in-flight drain/unknown-outcome semantics are required for a strong disconnect guarantee.
