# MCP client session lifecycle across OAuth disconnect and reconnect

## Finding

**Severity: Medium — stale remote-session reuse after credential replacement**

The OAuth-backed MCP connection manager creates clients whose token callback resolves credentials lazily on every request. This is useful for refresh rotation, but the client also retains its MCP `session_id` for its entire object lifetime. The runtime does not register created clients with the OAuth connection lifecycle, and the disconnect service has no mechanism to invalidate or close them.

As a result, a client created under one OAuth credential lifetime can remain usable after disconnect and, if the same owner/provider/account is reconnected, begin sending the new credential to the old MCP session.

This is distinct from stale MCP policy inheritance: even with policy revisions unchanged or independently handled, the protocol session itself is retained across credential lifetimes.

## Evidence

### 1. MCP client retains the server session identifier

In `core/mcp_client.py`, `MCPHTTPClient.__init__` initializes `self.session_id = None`. Each `_rpc()` call adds `Mcp-Session-Id` whenever this field is set, and then updates it from response headers. There is no public close, invalidate, or credential-generation binding method in the inspected client implementation.

### 2. The OAuth token callback is live, not lifetime-bound

In `core/oauth_mcp_connection.py`, `OAuthMCPConnectionManager.create_client()` closes over owner, provider and account identifiers. On each authorization-header request it calls `refresh_if_needed()`, then reads the current credential from the vault. It does not capture a credential lifetime identifier or generation when the client is created.

### 3. Disconnect removes the credential but does not invalidate MCP clients

In `core/oauth_disconnect_service.py`, disconnect retrieves and optionally revokes the credential, then deletes it from the vault. The service does not notify the MCP connection manager, revoke client objects, clear their session IDs, or send an MCP session termination request.

### 4. Runtime returns clients without lifecycle tracking

In `core/mcp_runtime.py`, `create_client()` returns the manager-created client directly. The runtime does not retain a registry of live clients keyed by owner/provider/account, and there is no lifecycle generation checked by the client before each request.

## Impact and conditions

A client created before disconnect can no longer authenticate while the credential is absent. However, if the same owner/provider/account is reconnected before that client is discarded, its live callback can resolve the replacement credential while the client continues to send the old `Mcp-Session-Id`.

Whether the remote server accepts this combination depends on its session implementation and token/session binding. The repository does not establish provider-side behavior, so this is not proof that every server will accept a stale session. The local client nevertheless permits cross-lifetime session reuse and has no fail-closed boundary preventing it.

Potential consequences include reuse of server-side session state created under the previous authorization, inconsistent authorization context, or requests being associated with stale server-side resources after reauthorization.

## Recommended remediation

1. Introduce a durable connection-lifecycle generation (or opaque credential-lifetime ID) that changes on disconnect and every new authorization.
2. Capture that generation when creating an MCP client and require it to remain current before every RPC, including initialization, notifications, discovery and tool calls.
3. Track live clients by owner/provider/account/lifecycle generation. On disconnect, invalidate them synchronously before credential deletion is considered complete.
4. Clear the local session ID and, where supported, send the protocol's session termination request using the old credential before revocation/deletion. Treat remote termination as best-effort; local invalidation must be authoritative.
5. Ensure reauthorization never revives clients from an earlier generation, even when owner/provider/account identifiers are unchanged.
6. Add regression tests: create client and establish session; disconnect; reconnect same account; assert old client cannot send any RPC or attach the replacement token to its old session. Also test a disconnect racing an in-flight request and a new client created after reconnect.

## Scope and verification limits

This is a source-level finding. No live MCP server was used, and provider-specific session semantics were not verified. No runtime tests were executed during this audit. The finding is about the missing local lifecycle fence, not an assertion that a particular provider accepts stale session IDs.

## Related existing audits

- `docs/OAUTH_DISCONNECT_MCP_POLICY_LIFECYCLE_AUDIT.md` — persistent MCP policy inheritance after disconnect/reconnect.
- `docs/OAUTH_PROVIDER_CONFIGURATION_CREDENTIAL_BINDING_AUDIT.md` — credentials are not bound to provider configuration generations.
- `docs/OAUTH_REFRESH_REAUTHORIZATION_ABA_RACE_AUDIT.md` — stale refresh responses across credential deletion/recreation.

Those audits cover policy and credential state. This document addresses the separate lifetime of already-created MCP protocol clients and their retained server session identifiers.
