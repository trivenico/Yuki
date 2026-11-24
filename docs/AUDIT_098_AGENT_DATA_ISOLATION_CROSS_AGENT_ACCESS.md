# Audit 098 — Agent Data Isolation & Cross-Agent Access Control

**Repository:** trivenico/Yuki  
**Base:** main  
**Scope:** conversations, task persistence/idempotency, specialist coordination, memory, OAuth credentials, knowledge graph.

## Executive summary

The reviewed conversation persistence path applies owner filters to individual conversation operations. OAuth credential and connection paths also have owner-scoped operations in their reviewed APIs. However, the workflow task journal has no owner/tenant column and uses a globally unique idempotency key.

A concrete cross-actor isolation defect exists in TaskRuntime.enqueue: after authorizing task creation for an actor, it passes the caller's raw idempotency key into a globally unique workflow_runs keyspace. If another actor submits the same key and identical workflow definition, WorkflowRunStore.create returns the existing run. TaskRuntime.enqueue then returns that run's definition and task metadata to the second actor without proving ownership. If definitions differ, it can reveal that the key is already in use through a conflict. This is a cross-actor task collision and potential data disclosure.

Implementation PR #1172 scopes the store idempotency key to a SHA-256 digest of authenticated actor ID plus caller key, preserving same-actor retries while separating actors. This is a focused mitigation, not full tenant ownership enforcement.

## Reviewed source

- core/conversation_store.py
- core/conversation_service.py
- core/conversation_application_runtime.py
- automation/workflow_store.py
- automation/task_runtime.py
- automation/agent_coordinator.py
- companion/memory.py
- core/vector_memory.py
- knowledge/knowledge_graph.py
- core/oauth_connection_registry.py
- core/oauth_credential_vault.py
- api/conversations.py
- tests/test_conversation_store.py
- tests/test_task_runtime.py
- tests/test_agent_coordinator.py

## Findings

### P1 — Global workflow idempotency namespace crosses actors

WorkflowRunStore defines idempotency_key as globally UNIQUE. TaskRuntime.enqueue authorizes actor creation but passes raw idempotency_key to the global store. A second actor using the same key and identical payload receives the first actor's run from WorkflowRunStore.create; enqueue returns the stored task record. No owner field or ownership comparison prevents this.

**Remediation submitted:** TaskRuntime now hashes actor identity and caller idempotency key into an actor-scoped internal key. Same-actor retries remain idempotent; different actors no longer collide.

Regression tests cover same key across actors and same-actor retry. Tests added but not executed.

**Residual:** task records still lack durable owner/tenant columns. Actor-scoped idempotency prevents this collision but does not replace owner-bound read/list/dispatch/cancel authorization.

### P1 — Workflow records lack intrinsic owner/tenant ownership

workflow_runs and task_events persist task IDs, definitions, results, event actors, and worker labels, but reviewed schema has no owner_id/tenant_id/creator principal columns. TaskRuntime uses an injected authorization callback, and list operations are global to that authorized supervisor. This depends on trusted callback composition and cannot be independently enforced by the store.

**Recommendation:** persist tenant_id, owner_id, creator principal, and authorization domain in task rows; enforce owner predicates in store methods and tenant-scoped unique idempotency constraints. Migrate existing rows with an explicit owner-reconciliation policy rather than guessing ownership.

### P1 — Shared RoleEngine state can cross conversation owners

ConversationService stores one RoleEngine on the service instance. set_role mutates it and _system_guidance reads that shared role during each conversation. When the same service instance serves multiple owners, one caller's role selection can influence another owner's system guidance. This is a context-isolation issue, not evidence that roles grant tools or permissions.

**Recommendation:** use explicit per-conversation or per-owner role state resolved from trusted identity and durable storage. Do not replace it with an in-memory owner dictionary as a multi-worker persistence solution.

### P1 — Memory and knowledge graph principal boundaries are not intrinsic to their APIs

The reviewed MemoryStore/VectorMemory and KnowledgeGraph APIs do not consistently accept owner/tenant/agent principal context. Their safety depends on composition selecting distinct protected stores or applying a trusted gateway. Shared instances can become cross-agent data boundaries if callers do not isolate them.

**Recommendation:** mediate memory/graph access through principal-aware services, persist namespace metadata, and enforce owner/tenant/agent ACLs in retrieval, mutation, export, and deletion paths.

### P2 — Conversation persistence has stronger owner predicates, but service guidance is shared

ConversationStore's reviewed get/list/append operations filter by owner_id and hide unauthorized conversation IDs. ConversationService relies on callers supplying authenticated owner_id; it does not authenticate network requests itself. Shared mutable role guidance remains a separate cross-owner context risk.

### P2 — OAuth credentials have owner-scoped registry operations, but not a universal agent identity model

The reviewed OAuth registry/vault paths bind credentials to owner/provider/account and protect token material. A future specialist must receive an explicit delegated connection capability rather than inheriting broad owner credentials or selecting arbitrary owner records.

### P2 — Supervisor-wide listings require explicit global authority

TaskRuntime list operations authorize a global task:list action and return runs from the shared store. The authorization callback must therefore represent a deliberate supervisor capability, not a generic authenticated-user permission.

## Implementation status

- PR #1172 contains actor-scoped idempotency and regression tests.
- Audit documentation is submitted separately in PR #1173.
- Tests were added but not executed in this session.
- No multi-tenant integration, concurrent collision test, or live cross-agent exploit test was run.
- No PR has been merged or deployed.

## Recommended control-plane model

Every durable object should carry:
- tenant_id
- owner_id
- creator principal / agent identity
- resource ID and type
- policy revision
- created/updated timestamps
- explicit sharing ACL or delegated capability reference

Every read, write, list, export, dispatch, and delete should enforce the same principal context. Agent-to-agent sharing should be an explicit, revocable grant with an auditable recipient and purpose. Global supervisor operations must be separate capabilities.

## Conclusion

A real cross-actor task collision was identified and a narrow actor-scoped idempotency fix submitted. The larger architecture still needs intrinsic tenant/owner fields and principal-aware memory, graph, and task services before Yuki can safely operate 100 agents over shared infrastructure.
