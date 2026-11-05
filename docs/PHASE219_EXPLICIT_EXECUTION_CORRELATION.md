# Phase 219 — explicit provider/workflow execution correlation

- Added a durable one-to-one mapping table between provider execution IDs and workflow operation IDs.
- Unique constraints prevent one execution mapping to multiple operations or one operation mapping to multiple executions.
- Mapping creation validates provider execution existence and provider identity.
- The reconciliation service independently validates the workflow operation and provider identity before linking.
- Identical repeated link requests are idempotent; conflicting links fail closed.
- The recovery control plane now checks explicit mappings and reports verified, orphaned, or provider-mismatched links.
- No links are inferred from timestamps, names, request digests or proximity.

## Trust boundary

A mapping establishes identity/correlation only. It does not prove remote provider completion. Evidence verification remains a separate responsibility. Provider and workflow journals remain separate SQLite databases, so the mapping is persisted in the provider journal and checked against the workflow journal on each control-plane snapshot.
