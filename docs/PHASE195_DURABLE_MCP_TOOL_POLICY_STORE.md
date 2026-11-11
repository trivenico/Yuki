# Phase 195 — durable MCP tool policy storage

## Delivered

- Persist trusted tool policies in SQLite, keyed by authenticated owner, provider, connected account and tool name.
- Persist a separate monotonic revision for each owner/provider/account scope. Empty scopes begin at revision 1.
- Update policy content and its scope revision in one `BEGIN IMMEDIATE` transaction.
- Require an exact expected registry revision for every set/remove operation (compare-and-swap).
- Require each tool's policy version to advance exactly once when it is updated.
- Retain revision rows after the final policy is removed. A delete/recreate cycle therefore cannot make an old registry snapshot current again.
- Load immutable `MCPToolPolicyRegistry` snapshots from persisted policy records.
- Treat malformed or corrupted stored policy data as a hard failure; never partially load or infer a safe policy.
- Bound policies per connected account and validate identities, tool names, risk classes and scopes before persistence.

## Runtime integration contract

Construct a store using the host-selected private application database path:

```python
store = SQLiteMCPToolPolicyStore(database_path)

snapshot = store.load_registry(
    owner_id=authenticated_owner_id,
    provider_id=trusted_provider_id,
    account_id=connected_account_id,
)

client = manager.create_policy_client(
    owner_id=authenticated_owner_id,
    account_id=connected_account_id,
    endpoint=trusted_endpoint,
    allowed_hosts=trusted_hosts,
    policy_registry=snapshot,
    granted_scopes=validated_oauth_scopes,
    owner_approval=owner_confirmation_callback,
    audit=durable_audit_callback,
    current_revision=lambda: store.current_revision(
        owner_id=authenticated_owner_id,
        provider_id=trusted_provider_id,
        account_id=connected_account_id,
    ),
)
```

Policy changes must use the revision returned by the latest snapshot/store read:

```python
next_revision = store.set_policy(
    owner_id=authenticated_owner_id,
    provider_id=trusted_provider_id,
    account_id=connected_account_id,
    policy=reviewed_policy,
    expected_revision=snapshot.revision,
)
```

The host must reload a snapshot after a successful mutation. Existing clients retain their old snapshot and are denied on their next invocation because the live revision has advanced.

## Concurrency and failure semantics

- SQLite `BEGIN IMMEDIATE` serializes writers. A second writer using the same expected revision receives `MCPToolPolicyConflict`; it must reload and re-evaluate rather than blindly retry.
- Policy data and revision increments commit or roll back together.
- Read paths use a single SQLite read transaction so revision and policy rows represent one consistent snapshot.
- Missing revision rows mean the initial revision 1. The first mutation creates the revision row and advances it to 2.
- Store corruption, invalid JSON, invalid risk values and invalid policy records fail closed.
- This module does not connect providers, enable tools automatically, approve external actions or store OAuth credentials.

## Deliberate next boundary

This phase provides durable policy state and immediate revision fencing. It does not yet claim that remote execution outcomes are audited. A subsequent execution-lifecycle integration must distinguish authorization, dispatch, confirmed success, definite failure and ambiguous/unknown remote outcomes. Approval must never be represented as proof of execution.
