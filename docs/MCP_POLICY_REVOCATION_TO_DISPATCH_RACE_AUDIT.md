# MCP Policy Revocation-to-Dispatch Race Audit

## Scope

Source review at PR #1346 head `89ee8dd89ab23eb12d8c893bd03d9c19a14f075c`. This review examined the host-composed MCP runtime, immutable policy registry, durable policy revision store, MCP client dispatch path, execution audit adapter, and relevant policy/client tests.

This is a source-level finding. No runtime code was changed and no tests were executed.

## Finding: policy revocation can race the final approval audit and remote dispatch

**Severity: High (conditional on concurrent policy revocation and a consequential MCP tool call).**

The policy registry correctly binds authorization to owner, provider, account, tool name, required scopes, enabled state, and a durable registry revision. It also rechecks the live revision after the initial request audit and after a potentially long owner-confirmation prompt.

However, the final revision check is not the last operation before authorization returns:

- In the read-only path, `build_approver()` checks `revision_is_current()`, then appends `authorized_read_only`, then returns `True`.
- In the write/financial path, it checks the revision after owner confirmation, then appends `owner_approved` (or `denied`), then returns the confirmation result.

The audit append is a separate durable operation. A concurrent policy disable/removal can commit after the revision check but before the approval callback returns. The callback can therefore return `True` based on a stale registry snapshot.

There is a second dispatch gap in `MCPHTTPClient.call_tool()`: after the approver returns true, the client appends `execution_started` and only then sends the remote `tools/call` RPC. That audit write is another interval in which policy revision can advance. The client does not revalidate policy revision after that write or immediately before the network dispatch.

The relevant source sequence is:

1. `core/mcp_tool_policy.py`: `revision_is_current()` is checked before the final `authorized_read_only` / `owner_approved` audit append; the callback then returns true.
2. `core/mcp_client.py`: `call_tool()` trusts the callback result, writes `execution_started`, then calls `_rpc("tools/call", ...)`.
3. `core/mcp_tool_policy_store.py`: policy mutations atomically increment the owner/provider/account revision, but this revision mutation is not transactionally coupled to the MCP execution-start decision.

### Example interleaving

1. Client loads registry revision 7.
2. Tool call passes the live revision check at 7.
3. Owner confirms a write operation (or a read-only call passes its policy check).
4. The final approval audit append begins.
5. Another owner action disables/removes the tool, committing revision 8.
6. The approval audit append completes; the callback returns `True` without another revision check.
7. The MCP client appends `execution_started`.
8. The client sends the remote tool call even though the durable policy revision is now 8.

The same race can occur if revocation lands during the client's `execution_started` audit write after the approval callback has returned.

## Impact and boundaries

A tool disabled or removed before the remote dispatch can still be dispatched using an earlier approval decision. This weakens the documented immediate revision-fencing guarantee for existing clients.

The finding does **not** show that model input can choose another owner, provider, account, tool outside the allowlist, or fabricate the owner-approval callback. The callback is a trusted host dependency. The issue is the missing final serialization/revalidation point under concurrent policy mutation.

An already-dispatched remote request cannot generally be recalled by a later policy revocation. The system needs a defined linearization point: revocation should prevent any execution whose dispatch claim has not already been committed, while executions claimed before revocation may be treated as in-flight.

## Existing coverage and gap

The policy tests cover:
- revocation during the initial `requested` audit;
- revocation while the owner-confirmation callback is running;
- stale or unavailable revisions denying authorization;
- disabled policies being excluded from the allowlist.

The tests do not cover:
- revocation during the final `authorized_read_only` or `owner_approved` audit append;
- revocation during the `execution_started` audit append;
- an assertion that no remote `tools/call` transport request is sent after the durable revision advances in either interval.

## Recommended remediation

1. Introduce a dispatch-authorization claim that checks the expected policy revision and records the invocation start under a transaction/coordination boundary shared with policy mutations. Treat this successful claim as the explicit linearization point.
2. Ensure revocation and dispatch claims serialize against one another. If revocation commits first, the claim must fail; if the claim commits first, the operation is explicitly considered in-flight and cannot be promised cancellable.
3. Do not rely solely on a second non-atomic revision read immediately before the HTTP call; that still leaves a check-to-dispatch race.
4. Include a stable invocation identifier and the policy revision in the durable execution record so the owner can distinguish pre-revocation in-flight work from post-revocation dispatch attempts.
5. Add deterministic tests with injected audit hooks/barriers: advance the revision during the final approval audit and during `execution_started`; verify the dispatch claim is rejected and the fake transport receives no `tools/call` request. Also test the inverse ordering where the dispatch claim wins before revocation.

## Conclusion

This is a distinct concurrency gap in MCP policy revision fencing, not a duplicate of the earlier generic approval-session or structured batch findings. It warrants a focused fix and deterministic race tests before relying on policy revocation as an immediate execution fence.
