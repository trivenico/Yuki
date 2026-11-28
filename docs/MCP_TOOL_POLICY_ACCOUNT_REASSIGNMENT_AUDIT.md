# MCP Tool Policy Account-ID Reuse and Reassignment Audit

## Scope

Source review at PR #1346 head `4876fb5bf1b290bab42af8534049e6af5d37cee5`. Reviewed the durable MCP policy store, policy registry/approver, MCP runtime policy loading, OAuth connection registry, credential vault, and associated tests.

## Finding: enabled MCP tool policies survive connection deletion and can be inherited by a later identity reusing the same account key

**Severity: Medium — stale authorization inheritance after host-side account reassignment or ID reuse.**

The durable policy store keys policy rows and revision rows by `(owner_id, provider_id, account_id)`. The policy table has no external OAuth subject/identity binding or account-lifecycle generation. The store exposes per-tool `set_policy` and `remove_policy`, but no operation to invalidate or retire all policies for an account as part of account deletion/reassignment.

The OAuth connection registry and credential vault use the same owner/provider/account tuple. Their removal operations do not remove MCP policy rows or advance the MCP policy revision. The disconnect API removes the credential and connection registry metadata through separate services; it does not invoke policy-store cleanup.

When the host later reuses the same internal account ID for a different external identity under the same owner/provider tuple, `MCPRuntimeService.create_client()` loads the existing policy registry using that tuple. The approver then authorizes based on the retained enabled policy, current granted scopes, and current policy revision. If the new identity has the required scopes and the host-trusted tool definition remains configured, the old policy can authorize that tool without the new identity having explicitly enabled it.

This is not merely a stale client problem: it affects newly created clients after the new grant is established. The policy revision tombstone does not prevent it because it only prevents stale compare-and-swap writes; the old enabled policy and unchanged revision remain valid.

For read-only tools, the policy approver permits calls based on the enabled policy and scope checks without a fresh owner confirmation for each call. Write and financial tools require per-invocation owner approval, but still inherit the stale enabled policy state.

## Evidence

- `core/mcp_tool_policy_store.py`: `mcp_tool_policies` and `mcp_tool_policy_revisions` are keyed by owner/provider/account. The store provides per-tool set/remove operations and retains revision rows after final policy removal; it has no account retirement/reassignment API.
- `core/mcp_runtime.py`: `create_client()` loads policy state by owner/provider/account and supplies a current-revision callback. No connection identity generation or lifecycle marker is checked.
- `core/mcp_tool_policy.py`: `build_approver()` authorizes enabled snapshot policies when required scopes are present and the current revision matches. The immutable snapshot is therefore valid for the reused key unless the revision changes.
- `core/oauth_connection_registry.py`: connection rows are removed by owner/provider/account; removal does not coordinate with the policy store.
- `core/oauth_credential_vault.py`: credentials use the same tuple as their key. Deleting and subsequently creating a new credential under the same tuple does not create a new policy identity.
- `api/oauth_connections.py` and `core/oauth_disconnect_service.py`: disconnect removes OAuth credential and connection metadata, with no policy retirement step.
- `tests/test_mcp_tool_policy_store.py`: tests verify policy persistence and revision retention, but do not test account deletion/reassignment and subsequent identity reuse.

## Recommended remediation

1. Give each OAuth account connection a durable, non-reusable connection identity/generation that changes on every new authorization or host-side reassignment.
2. Bind MCP policy rows and policy snapshots to that connection generation (or to a stable external subject identity plus generation), not only the reusable account label.
3. On disconnect, account deletion, disablement, or reassignment, atomically advance/retire the policy generation so old policies fail closed for all new clients and existing approvers.
4. Require explicit policy re-approval for a new connection generation. Do not copy enabled state implicitly when an account label is reused.
5. Coordinate credential, connection-registry, and policy lifecycle through a durable transaction/outbox or idempotent reconciliation workflow; report partial cleanup rather than treating it as complete.
6. Add regression tests: enable a tool for identity A, disconnect/delete A, reauthorize identity B using the same owner/provider/account ID and scopes, then assert B cannot invoke the tool until a new explicit policy enablement is recorded. Include read-only, write, and financial risk classes.

## Boundaries

- The issue requires host-side reuse/reassignment of the same owner/provider/account tuple for a different external identity. Whether a deployment reuses account IDs this way is host-dependent.
- This review does not claim every host currently exposes an account reassignment endpoint. The policy store's keying and missing lifecycle invalidation make the unsafe reuse possible if host orchestration performs that operation.
- The existing revision tombstone is useful for preventing stale revision ABA after explicit policy mutations; it does not bind policies to the lifecycle of the external OAuth identity.
- This is distinct from credential configuration fingerprint binding and from the previously audited missing account-specific identity-verifier resolver.
- No runtime code was changed and no tests were executed during this source review.
