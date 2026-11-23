# Yuki Engineering Audits 057–059

**Repository:** `trivenico/Yuki`  
**Review date:** 2026-11-23  
**Status:** Architecture review and targeted source inspection; not a production certification.

> Scope note: these audits record the recommendations discussed in the engineering review and reconcile them with the repository files inspected on 2026-11-23. A proposed design is not an implemented feature. A source-level observation is not proof of runtime enforcement. No tests or production deployment were executed as part of this documentation change.

## Executive summary

The repository already contains meaningful guarded foundations: a non-executing task orchestrator, a default-deny tool-name policy, an explicit-handler worker, a bounded local task store, bounded local memory, a lexical search adapter, and a local knowledge graph. The next step is to harden their contracts and connect them through durable, tenant-aware, observable execution infrastructure.

The largest architectural gap is not a lack of agent concepts. It is the need to make identity, authorization, delegation, persistence, concurrency, lifecycle transitions, and memory provenance consistent across every execution path.

## Audit 057 — Agent skill execution and permission isolation

### Goals

- One authorization boundary for all skill and tool execution.
- Default-deny behavior for unknown tools and untrusted plugins.
- Independent validation of LLM-produced arguments.
- Explicit human approval for sensitive side effects.
- Auditable decisions without storing prompts, credentials, or sensitive payloads.
- Isolation of untrusted code and external tool servers.

### Source observations

- `yuki_security.py` defines a `SecurityPolicy` with a small default allowlist and rejects unknown tool names.
- `yuki_orchestrator.py` checks required tool names and can create approval requests, but its module contract explicitly says it prepares and checks tasks only; it does not execute tools.
- `yuki_agent_worker.py` runs only explicitly registered handlers, checks cooperative cancellation/deadlines, and avoids persisting raw exception messages.
- These controls are useful boundaries, but a tool-name allowlist alone is not a complete permission model. It does not express principal, tenant, resource, action, scope, expiry, or approval binding.

### Required controls

1. Define a canonical authorization decision with principal, tenant, agent identity, capability, action, resource scope, task ID, expiry, and policy version.
2. Re-authorize immediately before every side effect; never treat an earlier plan check as a durable grant.
3. Bind approvals to an immutable task digest, exact action/resource scope, requesting principal, expiry, and one-time nonce.
4. Ensure delegated agents can only receive a subset of the delegator's effective permissions.
5. Validate typed tool input at the execution boundary, not only in prompts or planners.
6. Treat plugin packages, MCP servers, browser content, retrieved documents, and tool outputs as untrusted data.
7. Run untrusted code in a separately verified sandbox. Configuration declarations and self-reported runtime metadata are not proof of isolation.
8. Keep audit events metadata-only, bounded, append-oriented, and protected against concurrent lost updates.
9. Fail closed when the policy service, approval store, audit sink, or identity provider is unavailable for a protected operation.
10. Add negative tests for bypass, replay, confused-deputy, cross-tenant, prompt-injection, and sandbox escape attempts.

### Acceptance tests

- Unknown skill/tool is denied.
- A valid tool name with an invalid principal or resource scope is denied.
- Approval for task A cannot authorize task B or a modified payload.
- Expired, replayed, or revoked approvals are denied.
- A child agent cannot exceed the parent's effective grants.
- An unavailable policy/approval service blocks protected side effects.
- Audit records contain no secrets, prompts, or raw tool arguments.

## Audit 058 — Multi-agent orchestration, delegation, and shared state

### Target architecture

```text
User / API
   |
   v
Identity + Tenant Context
   |
   v
Central Orchestrator
   |-- task graph / dependency validation
   |-- policy decision + approval binding
   |-- durable queue + idempotency
   |-- bounded scheduler / quotas
   |
   v
Agent Registry (explicit registrations only)
   |
   v
Worker Lease + Re-authorization
   |
   v
Isolated Handler / Tool Adapter
   |
   v
Output Schema + Provenance Validation
   |
   v
Durable Result + Audit / Trace
```

### Source observations

- `yuki_orchestrator.py` creates plans, checks required tool names, requests approval, and records lifecycle events. It deliberately does not execute tools.
- `yuki_agent_worker.py` uses an explicit handler mapping and a task store claim operation. Its cancellation and deadline checks are cooperative; handlers must still enforce their own bounded operations.
- `yuki_tasks.py` uses a file lock, atomic writes, and stale-snapshot rejection for local task-list mutations.
- These are useful local foundations, but they do not by themselves establish a distributed queue, multi-process lease correctness, cross-service transactions, tenant isolation, or exactly-once external side effects.

### Recommended module boundaries

Prefer the repository's existing runtime conventions and avoid creating duplicate task systems. If the orchestration package is introduced, keep it as a cohesive boundary:

```text
core/orchestration/
  coordinator.py
  agent_registry.py
  delegation.py
  task_graph.py
  task_queue.py
  scheduler.py
  message_bus.py
  state_manager.py
  concurrency.py
  retry_policy.py
  circuit_breaker.py
  result_validator.py
  execution_tracer.py
```

Before adding these modules, map them against the existing `yuki_orchestrator.py`, `yuki_agent_runtime.py`, `yuki_agent_worker.py`, `yuki_tasks.py`, `yuki_events.py`, and `yuki_workflow.py`. Consolidate rather than fork overlapping responsibilities.

### Non-negotiable invariants

1. Every task has a stable ID, tenant/principal context, immutable input digest, idempotency key, deadline, budget, and lifecycle state.
2. Every delegated task records parent task, delegator, delegate, allowed capabilities, resource scope, and expiry.
3. Delegation is attenuating: child grants are a strict subset of the delegator's effective grants.
4. Queue claims use leases with owner, expiry, heartbeat, and fencing token; stale workers cannot commit after lease loss.
5. Idempotency is enforced at the durable side-effect boundary, not only in the queue.
6. Retries are bounded, classified, jittered, and safe for the operation's idempotency semantics.
7. A task graph rejects cycles, missing dependencies, excessive fan-out, and resource-budget overcommitment.
8. Shared-state writes use transactions or compare-and-swap/version checks.
9. Cross-tenant reads and writes are denied by storage-level scoping, not agent instructions.
10. Every terminal or blocked state is observable; a worker crash cannot leave a task permanently invisible.
11. Parent cancellation propagates to descendants according to an explicit policy.
12. Agent messages use versioned schemas, bounded payloads, authenticated sender identity, and provenance labels.

### Suggested persistence model

| Entity | Purpose |
|---|---|
| `agents` | Stable agent identities, lifecycle, registered capabilities |
| `agent_capabilities` | Capability metadata and version |
| `agent_permissions` | Principal/agent grants and resource scope |
| `agent_tasks` | Durable task lifecycle and immutable input digest |
| `task_dependencies` | DAG edges and dependency state |
| `agent_delegations` | Parent/child authorization records |
| `agent_messages` | Typed, authenticated inter-agent messages |
| `agent_approvals` | Digest-bound human decisions |
| `agent_state_versions` | Versioned shared state / compare-and-swap |
| `agent_execution_logs` | Metadata-only audit events and trace references |
| `agent_dead_letters` | Exhausted or non-retryable task records |

Use unique constraints for idempotency, foreign keys for ownership, tenant-scoped indexes, and transactions for related state transitions. Choose the persistence technology only after matching it to the deployment topology and recovery requirements.

### Failure-injection tests

- Two workers attempt to claim one task.
- Worker lease expires while the handler is still running.
- A stale worker tries to commit after a new worker has claimed the task.
- Queue delivers the same message repeatedly.
- Handler crashes after an external side effect but before recording completion.
- Parent task is cancelled while child tasks are running.
- Dependency graph contains a cycle.
- Child requests an unauthorized capability.
- Two agents update the same versioned record concurrently.
- Malformed or oversized agent message is received.
- A task is retried after its authorization or approval expires.
- Orchestrator restarts during every lifecycle transition.

## Audit 059 — Memory, knowledge, and retrieval integrity

### Source observations

- `companion/memory.py` is a bounded local JSON memory store with explicit deletion and clear operations. It supports note, preference, and lesson categories, and uses process and file locks for its memory file.
- `core/vector_memory.py` is currently a lexical token-overlap search adapter over `MemoryStore`; despite its name, it does not perform embedding generation or vector similarity search.
- `knowledge/indexer.py` indexes bounded text files from an explicitly configured workspace into memory chunks. The indexed record currently retains a source filename in its text but does not persist a full source digest, parser version, chunk offsets, or a durable re-index/deletion relationship.
- `knowledge/knowledge_graph.py` stores bounded entities and relations in local JSON and validates relation endpoints. Its synchronization uses an in-process lock and atomic replace, but does not use the shared inter-process file-lock helper used by the memory store. Concurrent multi-process graph writers therefore need explicit protection before multi-worker use.
- The graph and memory formats are local-file stores, not tenant-aware distributed storage. No claim of encrypted-at-rest storage, semantic retrieval quality, or cross-device synchronization should be made without additional implementation and verification.

### Memory architecture target

Separate memory by purpose and trust:

| Memory class | Contents | Default policy |
|---|---|---|
| Working memory | Current task context and scratch state | Short-lived; task-scoped |
| Episodic memory | User-approved interaction summaries | User/tenant scoped; retention-bound |
| Semantic memory | Stable facts and concepts | Provenance and confidence required |
| Preference memory | Explicit user preferences | Opt-in; editable and deletable |
| Procedural memory | Approved workflows and lessons | Versioned; reviewed before reuse |
| Agent memory | Agent-specific operational context | Isolated by agent and tenant |
| Evidence store | Source excerpts, hashes, citations | Immutable source evidence; annotations separate |

### Required record envelope

Every persisted memory item should carry:

- Stable memory ID and schema version.
- Tenant/user scope and allowed readers.
- Memory class and sensitivity classification.
- Source type and source reference.
- Created, updated, last-accessed, and expiry timestamps as applicable.
- Content digest and source digest where available.
- Extraction method/model/version where applicable.
- Confidence and verification status, kept distinct.
- Consent/retention policy and deletion lineage.
- Provenance links to source task and source artifact.

Do not treat an LLM-generated summary as verified fact. Keep source evidence separate from model interpretation and user correction.

### Retrieval and knowledge controls

1. Keep lexical search as a deterministic fallback; rename or document it accurately until real vector indexing exists.
2. If embeddings are introduced, version the embedding model, dimensions, normalization, chunking, and index schema.
3. Apply tenant/user/agent authorization filters before ranking and before returning retrieved content.
4. Use hybrid retrieval (lexical + semantic) with explicit filters and bounded top-k; do not let relevance scores override access control.
5. Preserve citations/source IDs through chunking, summarization, and agent handoffs.
6. Treat retrieved content as untrusted input; prompt-injection defenses must apply to memory and knowledge retrieval.
7. Add freshness, expiry, deduplication, contradiction detection, and re-index workflows.
8. Provide user-visible inspect, correct, export, and delete flows with deletion propagated to derived chunks, indexes, caches, and graph edges.
9. Support backup/restore and schema migrations with integrity checks.
10. Evaluate retrieval using a curated query set: recall@k, precision@k, citation correctness, stale-fact rate, cross-tenant leakage, and prompt-injection resistance.

### Immediate code-level follow-ups

- Add inter-process locking and concurrency tests to `knowledge/knowledge_graph.py`.
- Add schema validation for every memory field, including IDs, categories, timestamps, and optional provenance.
- Add source digest, chunk index/offset, parser version, and source-to-chunk deletion mapping to `knowledge/indexer.py`.
- Add a memory lifecycle API that can delete source records and all derived records by lineage.
- Rename `VectorMemory` to a neutral retrieval name or clearly document the lexical implementation; reserve vector terminology for actual embedding-backed search.
- Define tenant/user scoping before introducing shared or remote memory infrastructure.
- Add migration/versioning and corruption-recovery tests for memory and graph stores.

### Acceptance tests

- User A cannot retrieve, mutate, or delete User B's memory.
- Agent A cannot read Agent B's private memory unless explicitly granted.
- Deleting a source removes or invalidates every derived chunk and index entry.
- Corrupt or unsupported schemas fail closed without overwriting the original data.
- Concurrent graph writes do not lose updates.
- Re-indexing the same source is idempotent and updates prior chunks safely.
- Every retrieved claim can be traced to a source or is explicitly labeled ungrounded.
- Prompt injection embedded in a retrieved document cannot authorize tool use or override system policy.
- Expired memories are excluded according to policy and eventually purged.
- Backup/restore preserves IDs, provenance, permissions, and deletion state.

## Prioritized implementation sequence

1. **P0 — Authorization contract:** unify identity, tenant, capability, resource scope, approval binding, and re-authorization.
2. **P0 — Durable task lifecycle:** transactional state changes, leases/fencing, idempotency, retry classification, cancellation, and recovery.
3. **P0 — Tenant isolation:** enforce scope at every persistence and retrieval boundary.
4. **P1 — Shared-state consistency:** graph file locking now; versioned transactional storage for multi-worker deployment.
5. **P1 — Memory provenance:** schema envelope, source digests, chunk lineage, retention, and deletion propagation.
6. **P1 — Observability:** correlation IDs, trace context, metrics, metadata-only audit, and dead-letter inspection.
7. **P2 — Retrieval quality:** hybrid retrieval, embedding versioning if justified, evaluation dataset, freshness and contradiction handling.
8. **P2 — Agent communication:** versioned authenticated messages and bounded fan-out.

## Verification status

This document is a design and source-inspection artifact. The following remain **not verified by this review**:

- Full repository test suite.
- Multi-process and distributed concurrency behavior.
- Production database transactions and queue semantics.
- Runtime tenant isolation.
- Sandbox enforcement under hostile workloads.
- Encryption at rest and backup recovery.
- Semantic/vector retrieval quality.
- Load, latency, and failure-recovery targets.

Do not mark any acceptance criterion complete until an automated test or independently reproducible operational check demonstrates it.
