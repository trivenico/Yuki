# Yuki knowledge lifecycle

Yuki memories now carry a lifecycle status independent of their expiry and correction lineage.

## Statuses

- `active`: stored and eligible for normal retrieval, subject to expiry and permissions.
- `approved`: owner-reviewed; approval marks the record verified.
- `rejected`: terminal review decision; excluded from normal retrieval.
- `retired`: terminal lifecycle decision; excluded from normal retrieval.
- Superseded records remain in correction history and are not returned by normal retrieval.

## Owner-controlled operations

`AgentMemoryStore.transition_lifecycle(...)` supports approval and rejection with `agent_memory:review`, and retirement with `agent_memory:retire`. Every transition records the previous and new status, actor, reason and timestamp. Rejected and retired memories cannot be reactivated; create a new memory and review it instead.

`AgentMemoryStore.renew(...)` extends an active or approved record's expiry and requires `agent_memory:renew` plus a non-empty rationale. It cannot revive rejected or retired claims.

Lifecycle events are append-only at the SQLite layer: update and delete triggers protect the audit table. Normal listing, paginated listing, lexical search and semantic retrieval all use the same active-record filters.

## Safety and scope

- Expiry remains enforced independently of lifecycle status.
- Low confidence does not automatically reject a memory.
- No model or agent can grant itself review, retirement or renewal permissions.
- Retrieved memory remains untrusted reference data.
- The current implementation records owner decisions; it does not yet synchronize lifecycle changes to remote specialist caches or replicas. Those need explicit invalidation events and delivery guarantees before distributed rollout.
