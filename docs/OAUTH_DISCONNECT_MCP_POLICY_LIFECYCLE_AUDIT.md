# OAuth Disconnect and MCP Policy Lifecycle Audit

## Scope

Source review at PR #1346 head `d1d5e9e2afd0ed13aa264e82e23ec0409b732ee3`. Reviewed OAuth connection registry, disconnect API/service, durable credential vault, MCP management API, MCP runtime, durable tool-policy store, policy registry, and relevant tests.

## Finding: disconnect leaves enabled MCP tool policies attached to the reusable account key

**Severity: Medium — stale authorization can be inherited after reconnect or account-ID reassignment.**

The durable MCP policy store keys both policies and their revision counter by `(owner_id, provider_id, account_id)`. The policy rows include enabled state, risk, required scopes, and per-tool version. There is no account lifecycle identifier or connection generation in that key.

The OAuth disconnect path does not mutate the policy store:

1. `AuthenticatedOAuthConnectionAPI` resolves the owner's connection and calls the provider disconnect service.
2. `OAuthDisconnectService.disconnect()` revokes remotely on a best-effort basis and deletes the vault credential.
3. The API then removes the row from `SQLiteOAuthConnectionRegistry`.
4. No call removes or disables MCP policies, advances their revision, or marks the policy scope retired.

The management API also has no account-wide policy cleanup operation. Its DELETE endpoint removes only one named tool policy. The durable policy store retains policy rows until individual policies are removed; its revision row is intentionally retained to fence old snapshots, but that tombstone does not deactivate the remaining policies.

On a later connection using the same owner/provider/account identifiers, `MCPRuntimeService.create_client()` loads the existing policy registry from the durable store. The manager derives the client's allowed tool names from enabled policies. The management API's policy listing likewise reads the same persisted rows. Thus, a reconnected account can inherit old enabled tool permissions without the owner reviewing those policies again.

This becomes an account-reassignment risk if the host reuses an `account_id` for a different external identity or tenant. A new OAuth grant can be stored under the same local key, while the old policy rows remain addressable under that key. The policy store has no immutable external-subject binding or connection-generation field with which to distinguish the old and new account lifetimes.

## Evidence

- `api/oauth_connections.py`: successful DELETE calls the disconnect service and then removes only the OAuth connection-registry row. It has no MCP policy-store dependency or cleanup callback.
- `core/oauth_disconnect_service.py`: deletes the vault credential; it does not interact with MCP policy state.
- `core/mcp_tool_policy_store.py`: `mcp_tool_policies` and `mcp_tool_policy_revisions` are keyed by owner/provider/account. There is no scope retirement, account deletion, or connection-generation API.
- `core/mcp_runtime.py`: `create_client()` loads the persisted policy registry for the supplied owner/provider/account; `list_policies()` exposes that same durable snapshot.
- `core/mcp_tool_policy.py`: enabled policies determine `tool_names()`; the policy registry is immutable but remains valid for the scope until its revision changes.
- `api/mcp_management.py`: policy DELETE removes a single tool policy. No account-wide lifecycle cleanup is coupled to OAuth disconnect.
- `tests/test_oauth_connections_api.py`: disconnect tests assert credential/connection cleanup behavior but do not assert policy cleanup.
- `tests/test_mcp_runtime.py` and `tests/test_mcp_tool_policy_store.py`: verify persistence across store restart and policy loading, but do not cover disconnect/reconnect or account-ID reassignment.

## Impact and boundaries

- A reconnect under the same local owner/provider/account tuple can restore previously enabled policies. The policy scope is not reset by OAuth disconnect.
- If the host reuses that tuple for a different external account, the previous account's tool allowlist, risk classifications, and required scopes can be inherited by the new grant.
- This is not a claim that a disconnected credential remains available: the disconnect service attempts local vault deletion. The issue is independent durable authorization state surviving credential deletion.
- The policy store's monotonic revision protects old snapshots when a policy mutation occurs. No such mutation is performed by the disconnect flow.
- Whether a particular deployment ever reuses account IDs, or separately performs policy cleanup outside these APIs, is host-orchestration dependent and is not established by this source review.

## Recommended remediation

1. Introduce a durable connection-lifecycle identifier/generation distinct from the reusable account ID. Bind each policy scope and MCP client to that lifecycle identity.
2. On disconnect, atomically retire the lifecycle generation so existing clients and newly loaded policy snapshots fail closed.
3. Ensure a new OAuth grant creates a fresh policy lifecycle, requiring explicit owner approval before any tool becomes enabled. Do not silently reactivate old policy rows.
4. If retaining historical policy/audit data, archive or mark the old policy scope retired rather than deleting evidence. Keep audit records append-only.
5. Make account reassignment an explicit operation that retires the old lifecycle and creates a new one; never infer continuity solely from matching provider/account strings.
6. Add regression tests: enable a tool, disconnect, reconnect with the same identifiers, and verify no tool is enabled; then repeat with a reassigned external subject and verify no old policy can authorize a call.

## Review limitations

No runtime code was changed and no tests were executed during this source review. Findings are based on the inspected source and tests only; external host cleanup workflows were not available for verification.
