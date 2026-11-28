# MCP Policy Mutation Audit Outcome Integrity Review

## Scope

Source review at PR #1346 head `eeff74554cef6ee4dd41a49b7ff6f729a5b4488e`. Reviewed:

- `core/mcp_runtime.py`: `set_tool_enabled()`, `remove_tool_policy()`, and `_audit_policy_mutation()`
- `core/mcp_tool_policy_store.py`: transactional compare-and-swap policy mutations
- `core/approval_audit.py`: append-only audit event semantics
- `api/mcp_management.py`: policy mutation response handling
- `tests/test_mcp_runtime.py` and `tests/test_mcp_tool_policy_store.py`

This is a source-level audit finding. No runtime code was changed and no tests were executed.

## Finding: policy mutation is recorded as approved before the durable state change is known to have succeeded

**Severity: Low — audit records can overstate the outcome of administrative authorization changes.**

Both `MCPRuntimeService.set_tool_enabled()` and `remove_tool_policy()` call `_audit_policy_mutation()` before invoking the durable policy-store mutation.

The helper appends an audit event with:

- `event_type="approved"`
- a fresh random `pending_id`
- the owner as actor
- a hashed skill identifier derived from provider, account, tool, operation, and expected revision

Only after that append succeeds does the runtime call `SQLiteMCPToolPolicyStore.set_policy()` or `remove_policy()`.

The policy store correctly performs its compare-and-swap inside `BEGIN IMMEDIATE`. It can reject the operation because another mutation advanced the scope revision after the runtime's preliminary registry read, because the per-tool version is stale, because capacity is exhausted, or because SQLite fails. In those cases the audit chain already contains an `approved` event for a mutation that did not commit.

There is also a process-crash window after the audit append and before the policy-store transaction commits. The audit record then survives without any corresponding durable policy change.

Conversely, the successful policy-store transaction is not followed by a distinct mutation-committed event. The audit event therefore cannot distinguish an owner-confirmed request from an applied state transition. The generic `approved` event does not include the resulting policy revision or a durable operation identifier that can be joined to the state change.

## Example interleaving

1. Owner A loads policy revision 4 and confirms enabling a tool.
2. Runtime A appends an `approved` audit event for the enable operation at expected revision 4.
3. Owner B or another process commits a policy mutation, advancing the scope to revision 5.
4. Runtime A calls `set_policy(... expected_revision=4)`; the store rejects it with `stale_policy_revision`.
5. The append-only audit chain still contains the earlier `approved` event, but no record says the requested enable failed or that revision 5 won.

The same ordering applies to disable and remove operations. The store's transactional CAS protects policy state from stale writes; it does not make the separately persisted audit event an accurate record of the mutation outcome.

## Impact and boundaries

- An operator or incident responder may interpret an `approved` audit event as evidence that a tool was enabled, disabled, or removed when the durable mutation actually failed or never ran.
- Concurrent policy administration can produce misleading event histories even though stale policy writes are correctly rejected.
- A crash can leave an approved event with no matching mutation result.
- This does **not** bypass owner confirmation, cause a stale policy write to succeed, or weaken the policy store's revision fencing.
- The issue is distinct from MCP tool approval-to-execution correlation: it concerns administrative changes to the durable policy registry, not correlation between a tool-call approval and its execution lifecycle.

## Existing coverage and gap

The reviewed policy-store tests cover transactional revision checks, stale revisions, per-tool version checks, and persistence. The runtime tests cover client creation and policy revision fencing.

The reviewed runtime tests do not inject a conflict or store failure after `_audit_policy_mutation()` and assert that the resulting audit history accurately represents a failed mutation. They also do not cover process interruption between the audit append and policy-store commit.

## Recommended remediation

1. Give each policy mutation a stable operation ID and explicit lifecycle semantics. Distinguish at least `requested/confirmed`, `committed`, and `failed`; do not use a generic `approved` event as proof of durable application.
2. Record the resulting registry revision and operation outcome. A successful mutation record should identify the exact revision committed.
3. Prefer a durable outbox/operation journal whose state transition is transactionally coupled to the policy-store mutation. Because the current policy and audit stores are separate SQLite databases, a plain second append after commit still leaves a crash window; do not describe that as atomic.
4. If cross-database atomicity is not introduced, use an explicit pending operation plus idempotent reconciliation. On conflict or failure, append a terminal failure outcome; on restart, reconcile pending operation IDs against the policy state and revision.
5. Keep the audit metadata-only: do not add raw tool arguments, access tokens, or remote tool results.
6. Add deterministic tests for: stale revision after the preliminary load, per-tool version conflict, capacity/store failure, crash/restart after the initial audit event, and successful mutation with an exact committed revision. Assert that audit consumers can distinguish each outcome.

## Conclusion

The policy store's compare-and-swap correctly protects durable authorization state. The separate audit append currently precedes that transaction and is labeled `approved`, so it can survive a rejected or interrupted mutation without a matching outcome. This is a bounded audit-integrity issue; it does not demonstrate an authorization bypass.

