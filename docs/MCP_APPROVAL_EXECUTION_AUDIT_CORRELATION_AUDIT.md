# MCP Approval-to-Execution Audit Correlation Gap

## Finding

**Severity: Low — durable audit records cannot reliably associate an approval decision with the execution it authorized**

The MCP policy layer records authorization decisions, while the MCP client records execution lifecycle events. Both are durable and metadata-only, but they use independent correlation identifiers.

In `core/mcp_tool_policy.py`, the policy approver calls the audit callback for `requested`, `authorized_read_only`, `owner_approved`, or `denied`. The callback receives policy metadata and has no invocation identifier.

In `core/approval_audit.py`, `MCPToolPolicyAudit.__call__()` creates a fresh `uuid.uuid4().hex` as the audit log's `pending_id` for each policy event.

Only after the approver returns true does `core/mcp_client.py` create a separate `invocation_id` for `execution_started`, `executed`, `execution_failed`, and `outcome_unknown`. Those execution events share the invocation ID, but there is no durable link back to the policy decision event.

Both adapters hash the provider/account/tool identity into the same general skill identity, and the audit log hashes the actor. Those fields identify the owner and tool scope but do not disambiguate concurrent calls to the same tool.

## Example

Two concurrent calls by the same owner to the same provider/account/tool can each produce a policy approval event and an execution-start event. The event stream contains:
- two policy decision records with unrelated random pending IDs;
- two execution lifecycles with separate invocation IDs;
- matching hashed owner and tool identity values.

The audit chain proves event ordering and integrity, but does not prove which approval record belongs to which execution lifecycle. This ambiguity remains even if each individual policy decision and execution event was written successfully.

## Impact and boundaries

- Incident responders cannot reliably reconstruct the approval-to-dispatch relationship for concurrent calls to the same tool.
- Compliance review may be unable to demonstrate that a specific consequential execution had a corresponding owner approval, rather than merely showing that some approval for that tool occurred nearby in the event stream.
- The gap weakens forensic accountability; it does **not** itself authorize a tool call, bypass policy, expose credentials, or demonstrate that an unapproved call can execute.
- Existing execution correlation remains useful: events within one execution lifecycle share the invocation ID.
- The audit log intentionally omits raw arguments and credentials. This finding does not recommend logging sensitive arguments.

## Recommended remediation

1. Generate a host-side invocation/correlation ID before entering the approval callback.
2. Thread that ID through the policy approval request and all policy audit events, then reuse the exact same ID for execution lifecycle events.
3. Preserve the existing hashed-at-rest treatment and metadata-only policy; do not add raw arguments, access tokens, or remote results to audit records.
4. Define terminal semantics for denied or rejected approvals so the correlation ID cannot be mistaken for a dispatched execution.
5. Add deterministic tests with two interleaved calls to the same tool. Assert each approval, denial, execution-start, and terminal event can be joined unambiguously by the same correlation ID, including an approval that is denied or whose audit append fails.
6. Consider a durable pre-approval invocation record or explicit lifecycle state if audit consumers need to distinguish an approval attempt from a dispatch attempt after process interruption.

## Scope and verification limits

Reviewed at PR #1346 head `fa504bc02e51e4c4f07f14598c3ef9e47d5eefca`:
- `core/mcp_tool_policy.py`
- `core/mcp_client.py`
- `core/mcp_runtime.py`
- `core/approval_audit.py`
- `tests/test_mcp_tool_policy.py`
- `tests/test_mcp_runtime.py`
- `docs/PHASE196_MCP_EXECUTION_AUDIT.md`

Source inspection only. No runtime tests, concurrent execution tests, or live provider calls were executed. The report does not claim that a particular audit event sequence has occurred in production.
