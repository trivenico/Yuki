# OAuth MCP Live Account and Connection Revocation Audit

## Scope

Source review at PR #1346 head `80cbc1a5e2a3d1dbd764ee99ddf040fac031ed74`. Reviewed OAuth MCP connection manager, MCP HTTP client, credential vault, connection registry, authenticated connection API, disconnect service, and their tests.

## Finding: existing MCP clients do not revalidate account or connection liveness before obtaining bearer tokens

**Severity: Medium — revocation and account-disablement enforcement gap for long-lived clients.**

`OAuthMCPConnectionManager.create_client()` captures the supplied `owner_id` and `account_id` in an `access_token_provider` closure. When the MCP client performs an RPC, `MCPHTTPClient._authorization_header()` invokes that callback. The callback calls `refresh_if_needed(owner_id, account_id)` and then reads the credential vault using owner/provider/account.

The callback does not consult:
- the authenticated session or current account status;
- the OAuth connection registry to confirm the connection remains registered;
- an account lifecycle/revocation generation;
- a manager/client shutdown or revocation signal.

The connection registry is only consulted by the authenticated MCP management API when it handles a new discovery/policy request. Existing MCP clients retain their callback closure and do not re-check that registry. Similarly, the authenticated disconnect API deletes the vault credential and registry row, but it has no reference to already-created MCP client instances and no mechanism to revoke their local execution capability.

If a client is retained after its owner is disabled/deleted or its connection is administratively removed, the callback can continue using any still-present vault credential. Deleting only the registry row does not stop it. If the credential is deleted, the next callback fails because the vault lookup returns no credential, but that is a separate required action and is not guaranteed by registry removal alone.

The current tests exercise lazy token lookup and credential absence, but do not test account disablement or registry removal against an already-created client.

## Evidence

- `core/oauth_mcp_connection.py`: the token callback closes over owner/account IDs and uses refresh service plus vault lookup; there is no live account/session/connection predicate.
- `core/mcp_client.py`: `_authorization_header()` invokes the callback for each RPC, providing a natural enforcement point, but only validates the returned token.
- `api/mcp_management.py`: authenticated API checks the current session and connection registry for management operations; this check is not passed into already-created MCP clients.
- `api/oauth_connections.py`: disconnect removes credential through the disconnect service and then removes registry metadata; no active-client invalidation hook is present.
- `core/oauth_disconnect_service.py`: local vault deletion is performed, but no in-memory client revocation registry is updated.
- `tests/test_oauth_mcp_connection.py`: tests lazy token lookup and missing credential behavior, not session/account disablement or connection revocation after client creation.

## Recommended remediation

1. Require a host-owned live authorization callback when constructing an OAuth MCP client. It should validate the owner/account is active and the connection is still authorized immediately before every token retrieval.
2. Add a durable connection/account generation or revocation epoch. Capture it at client creation and compare it with the current authoritative value on each token callback; mismatch fails closed.
3. Make account disable/delete operations atomically advance the revocation epoch and invalidate credentials/connection metadata through a durable lifecycle workflow.
4. Ensure disconnect revokes the durable generation before returning success, so pre-existing clients fail even if their Python objects remain alive.
5. Provide explicit client shutdown/invalidation for prompt local cleanup, but do not rely on in-memory invalidation as the only cross-process enforcement.
6. Add tests that create a client, then disable the account, remove the connection, or advance the revocation generation; every subsequent RPC authorization callback must fail without reading or refreshing the credential.

## Boundaries

- The authenticated OAuth and MCP management endpoints re-resolve the session on each HTTP request. This finding concerns already-created MCP clients and their token callback, not those API authentication checks.
- A successful local credential deletion makes subsequent vault lookup fail. This report does not claim that a deleted credential remains usable from the vault.
- Remote revocation is best-effort and may fail; local credential deletion is still attempted.
- Whether a host discards all MCP client instances on account deletion is not established by these source files. The manager API itself does not enforce that lifecycle contract, so safety currently depends on host orchestration.
- No runtime code was changed and no tests were executed during this source review.
