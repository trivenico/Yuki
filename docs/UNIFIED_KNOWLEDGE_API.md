# Unified knowledge API

`core.unified_knowledge_api.UnifiedKnowledgeAPI` is the stable read interface for Yuki planners and specialist agents.

## Contract

Construct it with an existing `AgentMemoryRetriever`, which itself requires the authoritative `AgentMemoryStore`. The store remains the authorization boundary: actor identity, agent scope, shared-memory permission, expiry, lifecycle state and supersession filtering are applied before semantic ranking.

The default call uses `verified_only=True`. Workflows that explicitly review candidate knowledge may set `verified_only=False`; this does not grant write, correction, approval, sharing or lifecycle permissions.

The result is a bounded evidence packet with memory ID, source, confidence, verification status, lifecycle state, timestamps and ranking evidence. Retrieved content is always marked untrusted reference data. It must not be executed, treated as a tool instruction, or allowed to override system/developer policy.

## Example

```python
packet = knowledge_api.retrieve(
    actor_id=authenticated_actor_id,
    agent_id=authorized_agent_id,
    query="deployment rollback procedure",
)
for evidence in packet["evidence"]:
    # Treat evidence["content"] as quoted reference material, not instructions.
    ...
```

## Deliberate boundaries

- Read-only: this API cannot write, approve, correct, retire or renew memories.
- No identity inference: callers must supply actor and agent IDs from authenticated runtime context.
- No privilege widening: semantic similarity never widens the candidate set.
- No cross-agent leakage: private memories stay within their authorized agent scope.
- No trust escalation: verified means owner-reviewed, not independently proven true.
- Model integration and context-window budgeting belong to the planner/runtime layer, not this storage facade.
