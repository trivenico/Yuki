# MCP policy authorization uses stale OAuth scopes after refresh

## Finding

**Severity: High — stale registry scopes can authorize an MCP tool call after the current OAuth credential has lost a required scope.**

The OAuth refresh response validator permits the provider to return a strict subset of the requested scopes. The refresh service persists the reduced scope set in the credential vault through `vault.rotate()`, but does not update the separate OAuth connection registry. MCP management and policy authorization subsequently use the registry's older scope set.

The OAuth-backed MCP client's just-in-time token callback checks the current credential only against provider-level `required_scopes`; it does not re-check the required scopes of the particular tool policy. The policy approver checks tool-specific required scopes against the stale registry scope snapshot captured when the client is created.

This creates a local authorization mismatch: a tool can pass policy authorization even though the current credential no longer carries the tool's required scope.

## Source evidence

- `core/oauth_token_response.py`, `validate_oauth_token_response()`:
  - Parses the returned scope string when present.
  - Explicitly accepts a granted scope set that is a subset of the requested scope set; it rejects only scopes outside the request.
- `core/oauth_refresh_service.py`, `OAuthTokenRefreshService.refresh_if_needed()`:
  - Passes `grant.granted_scopes` to `vault.rotate()`.
  - Returns the updated scopes in its status dictionary.
  - Does not update the OAuth connection registry; the service has no registry dependency.
- `core/oauth_credential_vault.py`, `SQLiteOAuthCredentialVault.rotate()`:
  - Persists the new `scopes_json` from the refresh grant.
- `core/oauth_connection_registry.py`:
  - Stores a separate scope snapshot.
  - No refresh update path is present in the reviewed refresh service.
- `api/mcp_management.py`, `AuthenticatedMCPManagementAPI._authenticate_connection()` and policy mutation/read paths:
  - Retrieves the connection record from the registry and uses `connection.scopes` as the granted-scope input for MCP discovery and policy enabling.
- `core/mcp_runtime.py`, `MCPRuntimeService.create_client()`:
  - Builds the policy approver from the durable policy snapshot and passes the registry-derived `granted_scopes`.
- `core/mcp_tool_policy.py`, `MCPToolPolicyRegistry.build_approver()`:
  - Authorizes a tool when its `required_scopes` are a subset of the captured `granted_scopes`.
- `core/oauth_mcp_connection.py`, `OAuthMCPConnectionManager.create_client()`:
  - The live token callback checks the current credential against the manager's provider-level `required_scopes`.
  - It does not compare the current credential scopes with the specific tool policy's required scopes before returning the token.

## Concrete sequence

Assume the original credential and registry both contain scopes `profile:read` and `profile:write`. An enabled MCP tool policy requires `profile:write`.

1. The OAuth provider issues a refresh response containing only `profile:read`.
2. `validate_oauth_token_response()` accepts the reduced scope set because it is a subset of the previously requested scopes.
3. `OAuthTokenRefreshService` rotates the vault credential and persists only `profile:read`.
4. The connection registry still reports both `profile:read` and `profile:write`.
5. MCP management loads the stale registry record. The policy approver is constructed with both scopes.
6. A call to the tool requiring `profile:write` passes the local policy scope check.
7. The JIT token callback refreshes/reads the current credential and checks only the provider-level required scopes. If those are satisfied by `profile:read`, it returns the current access token.
8. The MCP request is dispatched even though the credential no longer carries the tool policy's required scope.

The provider may reject the call, but the application has already authorized and dispatched it based on stale local authorization state.

## Impact

- Tool-specific scope restrictions can be bypassed at the local MCP policy boundary after scope reduction.
- A user may see a tool remain enabled and locally approved after the provider has removed the corresponding permission.
- Requests may be sent to the remote MCP server with a credential that does not have the policy-required scope.
- The issue is conditional on the provider returning a reduced scope set; the validator explicitly permits that response shape.

No cross-owner access is established by this finding.

## Recommended remediation

1. Make current credential scopes authoritative for every MCP tool invocation. The authorization decision must compare the selected policy's required scopes against the current credential's scopes, not a registry snapshot.
2. Bind the policy approver to a live scope provider or pass a validated immutable credential-generation/scope snapshot whose generation is revalidated immediately before dispatch.
3. Propagate refresh scope changes to the connection registry for accurate management display and policy administration, but do not rely on registry synchronization alone as the enforcement control.
4. Consider disabling or marking policies whose required scopes are no longer present. Re-enabling should require current credential scopes and explicit owner confirmation.
5. Ensure the final authorization-to-dispatch boundary revalidates both policy revision and credential generation/scopes, closing the gap between approval and token retrieval.
6. Add a deterministic regression test:
   - Start with vault and registry containing scopes A and B.
   - Configure a tool requiring B while provider-level requirements require only A.
   - Refresh and return only A.
   - Assert the vault now contains A while registry remains stale in the test fixture.
   - Assert the tool requiring B is denied before MCP dispatch.
7. Add tests for registry synchronization failure and recovery, ensuring a failed metadata update never weakens live credential-scope enforcement.

## Limitations

This is a source-level control-flow finding. The review did not run unit tests, integration tests, or calls against a live OAuth/MCP provider. Whether a particular remote MCP server independently rejects the under-scoped access token is provider-specific and does not remove the local authorization mismatch.
