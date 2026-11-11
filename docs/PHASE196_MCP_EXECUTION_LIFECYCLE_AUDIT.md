# Phase 196 — MCP execution lifecycle audit

## Goal

Separate authorization from remote execution. A successful approval is not proof
that a provider received, completed or persisted the requested action.

## Shared audit log

This phase extends the existing `SQLiteApprovalAuditLog` event allow-list and
adds `MCPToolExecutionAuditor`, a fixed-schema adapter. It does not create a
second audit database or chain. MCP policy and execution events append to the
existing hash-chained approval audit table.

The adapter binds authenticated owner, trusted provider and connected account.
It hashes the identity tuple before passing it to the existing log; execution
IDs and actor identifiers are hashed by the log before persistence. Tool names
are stored as a bounded `mcp:` skill identifier (or a stable SHA-256 fingerprint
for unusually long names). Arguments, credentials, results and response bodies
are never passed to the audit log.

## Runtime wiring

Use one bound auditor for both policy authorization and execution lifecycle:

```python
audit_log = SQLiteApprovalAuditLog(private_database_path)
mcp_audit = MCPToolExecutionAuditor(
    audit_log,
    owner_id=authenticated_owner_id,
    provider_id=trusted_provider_id,
    account_id=connected_account_id,
)

client = manager.create_policy_client(
    owner_id=authenticated_owner_id,
    account_id=connected_account_id,
    endpoint=trusted_endpoint,
    allowed_hosts=trusted_hosts,
    policy_registry=policy_snapshot,
    granted_scopes=validated_oauth_scopes,
    owner_approval=owner_confirmation_callback,
    audit=mcp_audit,
    current_revision=current_policy_revision,
    execution_audit=mcp_audit,
)
```

The connection manager binds lifecycle events to its authenticated owner,
configured provider and connected account. The adapter rejects mismatched
identity context.

## Event lifecycle

Events for one execution share a random 128-bit execution ID. The ID is hashed
at rest by the existing audit log.

1. `requested` — exact enabled policy and required scopes were found.
2. `approved` or `denied` — read-only authorization or explicit owner decision.
3. `execution_started` — durably appended immediately before the remote RPC.
4. `executed` — a valid JSON-RPC response returned without the MCP tool-level
   `isError` flag.
5. `execution_failed` — a valid response returned with `isError=true`.
6. `outcome_unknown` — the RPC raised after the start marker; dispatch or remote
   completion may have occurred.

The start event must be durable before the client dispatches. If it cannot be
written, the client does not send the remote request. If a terminal event cannot
be written after a remote response, the caller receives
`MCPExecutionOutcomeUnknown` and must not blindly retry.

## Ambiguous transport outcomes

The current transport does not provide a trustworthy proof that a failed request
was never dispatched. Therefore any exception after the durable start marker is
conservatively classified as `outcome_unknown`, including failures that may
have occurred before the provider received the request. This avoids false claims
of non-execution at the cost of occasional manual reconciliation.

No automatic retry is performed. Do not infer provider idempotency support.
Where a provider offers a documented idempotency mechanism, a later integration
may use it only after provider-specific verification. Otherwise, inspect the
provider's authoritative account state before deciding what to do next.

## Failure and security properties

- Approval and execution are separate audit events sharing an execution ID.
- Audit failure before dispatch fails closed.
- Remote transport/response failure after dispatch is not reported as a definite
  non-execution.
- No raw arguments, credentials, tool results, or remote response content are
  persisted by this adapter.
- The shared audit chain remains verifiable through
  `SQLiteApprovalAuditLog.verify_chain()`.
- This does not activate any MCP provider or income-platform integration.
