# Phase 83 — Unified task runtime

## Scope

Phase 83 adds a task-oriented facade over a persistence port and durable append-only event stream. The current implementation uses the SQLite workflow journal as its existing adapter; the official MVP direction is PostgreSQL-backed distributed task truth with SQLite retained for local/test/offline use. This phase records task state and progress; it does not execute arbitrary code or provide an autonomous worker service.

- Idempotent task creation using the existing workflow store.
- Structured task records and existing workflow state/lease semantics.
- Versioned progress and artifact events stored in SQLite.
- Worker lease checks for completion and rejection of expired leases.
- Explicit authorization callback; missing, non-True, or failing authorization fails closed.
- Terminal events for completion, failure, and cancellation.
- Bounded event payloads and optimistic version checks.

## Distributed target architecture

The canonical task envelope includes task ID, tenant/owner, idempotency key, task type/schema version, dependency IDs, state/version, attempt count, lease owner/expiry/fencing generation, deadline, cancellation state, budget, required capabilities, trace/correlation IDs and immutable artifact references. The scheduler claims only dependency-ready work in a transaction. Multiple workers may process independent tasks concurrently, while per-tenant and global quotas prevent resource starvation.

PostgreSQL is the production authoritative adapter for multi-host task state, transactional claims, lease/fencing, uniqueness and recovery scans. SQLite remains a local adapter and must not be used for cross-host leases. Redis is optional dispatch/cache only; messages are hints, not truth. Large outputs belong in content-addressed object storage with authorization/provenance metadata in PostgreSQL. Vector indexes are derived and rebuildable; ACL checks use canonical records. All adapters pass a shared contract suite, and missing production dependencies fail readiness rather than silently falling back.

## Security boundary

The `authorize(actor_id, action, task_id)` callback must be supplied by trusted application code that resolves a real authenticated principal and checks task ownership/tenant access. Never derive actor identity or permissions from task payloads, request bodies, model output, or client-supplied role fields.

This is a service boundary, not HTTP middleware. Hosted authentication transport integration remains separate work.

## Recovery semantics

- Task creation is idempotent by caller-supplied key.
- State updates and event appends use optimistic version checks.
- A worker must own an active lease to complete a task.
- Expired leases must be marked interrupted by the existing store; side-effecting tasks are not automatically replayed.
- Cancellation updates lifecycle state. A future worker/adapter must observe cancellation and safely stop long-running operations.
- Task events record progress and artifacts as metadata; references do not prove an artifact's content or integrity.

## API sketch

```python
store = WorkflowRunStore("state/tasks.sqlite3")
runtime = TaskRuntime(store, authorize=trusted_authorization_callback)
task = runtime.enqueue(
    actor_id=principal.actor_id,
    idempotency_key=request_id,
    name="research-summary",
    steps=[{"name": "collect", "action": "emit", "argument": "collect sources",
            "requires_approval": False}],
)
```

Use `record_progress`, `record_artifact`, `complete`, `fail`, `cancel`, `get`, and `events` to manage lifecycle and metadata. The facade does not execute declared steps itself.

## CI

The Phase 83 workflow runs the new task runtime tests and existing workflow-store/engine regression suites on Ubuntu, Windows, and macOS with Python 3.11, and checks dependency consistency.

## Cross-engine consolidation finding (2026-11-25)

Repository inspection identified a second, independent task persistence engine:
`core/durable_task_engine.py` stores goals, tasks, dependencies, retry metadata, leases and run events directly in SQLite. It is not an adapter of `WorkflowRepository`, and it has no PostgreSQL implementation. In parallel, `automation.task_runtime.TaskRuntime` consumes `WorkflowRepository`, for which both `WorkflowRunStore` (SQLite) and `PostgresWorkflowRunStore` (PostgreSQL) exist.

This means the repository currently has two task lifecycle models and two event models. The presence of a PostgreSQL adapter for `WorkflowRepository` does not make the goal/dependency engine distributed. Deploying both as independent authorities would create split-brain task state, duplicate scheduling semantics, inconsistent lease guarantees and confusing recovery behavior.

### Required convergence direction

- Establish one canonical task/goal lifecycle domain and one authoritative event contract. Goal/dependency planning must be represented in that domain rather than maintained by a second independent queue.
- Preserve existing public APIs through compatibility facades during migration; do not perform a flag-day rewrite or silently move existing task records.
- Make PostgreSQL the authoritative multi-host backend for the distributed deployment. Keep SQLite as a supported single-process/local/offline adapter, not as a second distributed authority.
- Treat Redis as optional notification/dispatch acceleration only. A lost or duplicated notification must not lose or duplicate authoritative task state.
- Preserve dependency readiness, idempotent submission, retry budgets, cancellation, worker leases, fencing generations, tenant ownership and append-only events in the canonical contract.
- For PostgreSQL, claims and all lease-sensitive state/event mutations must be atomic. Worker mutations must be fenced by lease owner, generation and database-clock expiry. A stale worker must not complete, retry or append progress after losing its lease.
- Add a migration/reconciliation plan for existing goal/task records, including stable IDs, idempotency keys, dependency edges, terminal states and event history. Do not dual-write two authorities without a designed reconciliation protocol.
- Run shared semantic contract tests against SQLite and PostgreSQL, plus multi-connection/multi-process PostgreSQL contention tests. Backend-specific SQL tests remain additional coverage, not a substitute for the shared contract.

### Safe implementation sequence

1. Inventory every constructor and caller of `DurableTaskEngine`, `TaskRuntime`, `WorkflowRunStore` and `PostgresWorkflowRunStore`; classify which component currently owns each task type.
2. Define a canonical domain contract that can express both workflow steps and goal/dependency graphs without backend-specific types.
3. Extend the PostgreSQL schema and repository with dependency-aware claims, retry scheduling and goal aggregation under transactional constraints.
4. Implement the equivalent SQLite adapter and shared contract suite.
5. Migrate callers to one facade, retain compatibility shims, and provide an explicit data migration/rollback path.
6. Only then enable multiple distributed workers against PostgreSQL; Redis remains optional and non-authoritative.

### Current implementation status and guardrails

The PostgreSQL `WorkflowRepository` adapter already exists; it uses transactional `SKIP LOCKED` claims, database-clock leases, owner/generation fencing and tenant ownership. The generic contract tests currently demonstrate SQLite structural compatibility, while PostgreSQL has its own opt-in integration suite gated by `YUKI_TEST_POSTGRES_URL`. They are not yet one parameterized shared suite.

The separate `DurableTaskEngine` remains SQLite-only and does not currently expose the same lease-generation fencing contract. It must not be described or deployed as a distributed worker queue. No production multi-host rollout is authorized by this finding. Existing persisted tasks must not be silently re-created, dropped or split across the two stores.

## Expanded persistence inventory and ownership boundaries (2026-11-25)

A targeted source inspection found additional SQLite-backed subsystems adjacent to task execution. They must be classified by domain before any consolidation; not every SQLite table should become part of the task queue.

| Component | Current persistence | Domain ownership | Distributed-MVP direction |
|---|---|---|---|
| `automation.workflow_store.WorkflowRunStore` | SQLite | Canonical workflow-run records and worker lifecycle | PostgreSQL authoritative adapter; SQLite local/test adapter |
| `automation.postgres_workflow_store.PostgresWorkflowRunStore` | PostgreSQL | Existing shared workflow repository adapter | Extend under the same contract; do not create a parallel queue |
| `core.durable_task_engine.DurableTaskEngine` | SQLite | Goals, dependency graph, retry schedule and run events | Converge into canonical task/goal contract; SQLite local adapter only |
| `automation.agent_plan_repository.SQLiteAgentPlanRepository` | SQLite | Agent plan, subtask and dispatch bookkeeping | Decide which fields are planning projections versus authoritative task state; link subtasks to canonical task IDs and avoid a second execution lifecycle |
| `automation.agent_coordinator` specialist-agent registry | SQLite | Agent registration/capability and coordinator metadata | Keep as a distinct registry domain; add a repository port and PostgreSQL adapter only when multi-host coordinator ownership requires it |
| `yuki_agent_runtime.TaskStore` | SQLite | Legacy agent task records/events | Inventory callers and data ownership; migrate behind compatibility facade, never dual-write silently |
| `core.coding_tasks.CodingTaskStore` | SQLite | Coding-job records/results | Treat coding jobs as a task-type projection over canonical task IDs; preserve domain-specific artifacts/results separately |
| `core.task_lifecycle.SQLiteTaskLifecycleStore` | SQLite | Task lifecycle/event state | Determine whether this duplicates workflow/task events; merge only after event semantics and retention are reconciled |

### Architecture boundary rules

1. **One authoritative execution lifecycle.** Every executable unit has one canonical task ID, tenant/owner, idempotency identity, state version, attempt budget, deadline, cancellation state, lease owner/generation, and trace/correlation identity.
2. **Specialist plans are not a second queue.** A plan can own ordered/parallel subtasks and dependencies, but each executable subtask must reference the canonical task record. Plan state is a projection/aggregate, not independent worker ownership.
3. **Registries remain separate bounded contexts.** Agent identity, capability declarations, health and registration metadata are not task rows. They may use their own repository and PostgreSQL schema, but must not create duplicate execution state.
4. **Domain artifacts remain domain-owned.** Coding patches, generated files, research documents and model artifacts live in controlled artifact/object storage. Task rows hold opaque references, digests, ACL/provenance and lifecycle metadata, not large payload blobs.
5. **Events have one authoritative append contract.** Task state transitions and their corresponding event records commit atomically. Specialist views may consume projections; they must not independently invent authoritative task histories.
6. **Central typed backend configuration.** Production composition selects PostgreSQL for shared execution state and validates adapter capabilities at startup. Local SQLite is explicitly scoped to one process/node. No implicit fallback or per-agent backend selection.
7. **Optional infrastructure remains optional.** Redis is a delivery optimization; object storage stores large immutable outputs; vector indexes are derived search projections. None may decide whether a task exists, is owned, or has completed.

### Inventory caveats and next gates

This is a source-level inventory, not proof that each listed store is instantiated in production. Before changing schemas, enumerate all constructors, imports, runtime composition roots, CLI entry points and data files; identify whether each store is active, legacy, test-only or a projection. In particular, inspect the agent coordinator's SQLite registry separately from its task runtime: registry persistence should not be collapsed into execution persistence merely because both currently use SQLite.

The next safe code change is not a mass backend rewrite. First add a canonical task identity link to agent-plan subtasks and define idempotent plan-to-task dispatch/reconciliation semantics. Then adapt specialist callers through the canonical task facade. Production migration must include a dry-run inventory, stable-ID mapping, count/hash reconciliation, cutover gate and rollback procedure.

### Agent-plan dispatch: existing integration and remaining composition gap

A deeper inspection refines the inventory above:

- `PlanSubtaskRecord` already contains an optional `task_id`; the SQLite schema persists it.
- `AgentPlanRepository` already defines owner-scoped plan/subtask operations and fenced dispatch reservations.
- `PostgresAgentPlanRepository` already exists with PostgreSQL plan/subtask persistence and a dispatch outbox.
- `SpecialistAgentCoordinator` already dispatches through `TaskRuntime` using stable idempotency keys and records the canonical workflow task ID back on the plan subtask.
- However, the coordinator constructor currently instantiates `SQLiteAgentPlanRepository` directly from `database_path`. It does not accept the repository port or select `PostgresAgentPlanRepository` through production composition. Its specialist-agent registry is also a separate SQLite table in the coordinator.

Therefore the missing feature is not a new plan-to-task ID field or a new PostgreSQL plan adapter. It is **safe runtime composition and end-to-end transaction/reconciliation semantics**. The PostgreSQL adapter's own outbox and fencing behavior must be preserved; simply swapping the constructor would not prove that plan creation, dispatch reservation, task enqueue, dispatch completion and recovery are correctly composed across two transactional stores.

Next implementation gates:
1. Change coordinator composition to accept an injected `AgentPlanRepository` while retaining the SQLite constructor path as a compatibility default for local use.
2. Define and test the production composition root that supplies PostgreSQL plan repository and PostgreSQL `WorkflowRepository`; reject mixed or unintended local adapters in distributed mode.
3. Preserve at-least-once outbox delivery and stable task idempotency. Reconciliation must recover crashes after enqueue but before dispatch completion by looking up the same idempotency key and verifying tenant/owner binding.
4. Keep the specialist-agent registry as its own repository boundary; do not conflate registry records with executable tasks.
5. Add end-to-end tests using two independent PostgreSQL connections for reservation expiry/takeover, duplicate outbox delivery, enqueue replay, stale dispatch generation, owner mismatch and recovery after each cross-store crash window.

No code change should remove the existing SQLite compatibility path or claim production distributed composition until these gates pass.

### Repository-port compatibility audit: PostgreSQL adapter is not substitutable yet

The method-by-method comparison shows that `PostgresAgentPlanRepository` is not currently an implementation of the `AgentPlanRepository` Protocol, despite having similarly named operations. Do not inject it into `SpecialistAgentCoordinator` yet.

Concrete incompatibilities:
- `create_plan`: Protocol accepts one `AgentPlanRecord` and returns that record; PostgreSQL adapter accepts keyword fields and returns a mapping containing database rows.
- `get_plan`: Protocol parameter order/names are plan ID plus nullable tenant/owner scope; PostgreSQL implementation requires keyword-only non-null tenant and owner and returns a raw mapping.
- `reserve_dispatch`: Protocol accepts nullable scope and returns the protocol's `DispatchReservation`; PostgreSQL requires non-null scope and adds a TTL parameter, and returns a different reservation dataclass with `reservation_token`/expiry fields.
- `complete_dispatch`, `cancel_dispatch`, and `get_dispatch`: PostgreSQL uses required non-null scope and keyword-only arguments; the protocol's nullable-scope compatibility semantics differ.
- PostgreSQL additionally owns an outbox claim/mark/recovery API that the protocol does not expose. Its claim-generation fence must not be dropped by a superficial adapter.

The correct next change is a dedicated, typed adapter/conformance layer, not changing coordinator construction to accept any object with similarly named methods. Define a canonical dispatch-reservation value with explicit token, generation, state, expiry and task ID; define consistent owner scope rules; normalize plan records at the boundary; and model the outbox delivery operations separately from coordinator reservation operations. Then run static Protocol conformance checks and behavioral contract tests against both repositories. The production composition root must reject SQLite plan storage when distributed mode is selected.

The SQLite coordinator registry is another independent store and must remain isolated from plan/task lifecycle until its own repository port and distributed ownership semantics are designed. Do not couple registry persistence to the plan adapter merely to remove a SQLite import.

This audit is source-level. No coordinator injection or runtime adapter change has been made because the current interfaces are observably incompatible; forcing it would break runtime behavior or silently discard outbox guarantees.

### Follow-up source verification: narrow the PostgreSQL conformance gap

A fresh read of the current branch confirms that the PostgreSQL plan repository has progressed beyond the earlier broad mismatch summary. The following operations are already typed or structurally aligned:
- `list_subtasks` returns `tuple[PlanSubtaskRecord, ...] | None`, matching the port's value shape.
- `update_subtask` has the same conditional-update inputs and boolean result, with PostgreSQL enforcing non-null tenant and owner scope.
- `reserve_dispatch` exposes a `claim_token` compatibility property and carries reservation generation, state, task ID and expiry.
- `complete_dispatch`, `cancel_dispatch`, and `get_dispatch` implement the coordinator-facing operations while preserving PostgreSQL reservation fencing.
- PostgreSQL separately implements `claim_outbox`, `mark_enqueued`, and `recover_stale_outbox`; these are delivery-worker operations and must remain a distinct contract.

The remaining concrete conformance gaps are narrower:
1. `create_plan` still accepts keyword fields and dictionaries and returns a raw mapping, rather than accepting and returning `AgentPlanRecord`.
2. `get_plan` still returns a raw mapping rather than `AgentPlanRecord | None`.
3. PostgreSQL intentionally requires a non-empty tenant and owner for every operation, while the compatibility Protocol currently permits both values to be null together. Distributed-mode composition should enforce strict ownership; legacy unowned SQLite behavior must not be silently treated as a distributed-safe mode.
4. The two reservation dataclasses differ in shape: PostgreSQL has a reservation token and expiry, while the SQLite port value has a nullable claim token and no expiry. The adapter must normalize this without losing PostgreSQL expiry or generation fencing.
5. PostgreSQL's `create_plan` computes its plan ID from tenant, owner and client idempotency key. An adapter must preserve the coordinator's idempotency/replay behavior and validate that the stored canonical digest corresponds to the exact typed record.

Next implementation should introduce a dedicated PostgreSQL-to-port adapter (or deliberately revise the port with a migration plan), convert plan mappings into `AgentPlanRecord`, and add shared contract tests for create/replay/conflict, owner-scoped get/list, compare-and-set updates, reservation expiry/takeover, stale-generation completion, cancellation tombstones and outbox recovery. Keep outbox delivery methods separately typed. Do not wire distributed production composition until those tests and the owner-scope policy are enforced.

This follow-up supersedes any implication in the earlier audit that PostgreSQL lacks typed subtask reads or reservation fencing. It does not change the conclusion that direct coordinator injection is not yet safe.

### Plan identity compatibility: adapter must preserve the database-generated identifier

The coordinator currently derives `plan_id` from its scoped idempotency key before constructing `AgentPlanRecord`. The PostgreSQL adapter instead derives a canonical database identifier with an `ap_` prefix from tenant, owner and client idempotency key, and returns its persisted row bundle. It does not persist the caller-supplied `AgentPlanRecord.plan_id` as the authoritative identifier.

Therefore an adapter cannot merely convert the PostgreSQL row bundle into a record while retaining the caller's proposed ID. It must return the persisted PostgreSQL `plan_id` on both first create and idempotent replay, and every subsequent coordinator operation must use that returned ID. The idempotency key and request digest must also be preserved and validated against the persisted row.

This affects more than serialization: plan IDs are passed to get/list/update/reserve/cancel operations, linked to outbox rows, and may be referenced by audit and task metadata. A mixed identifier would cause lookups to miss or could create duplicate logical plans across adapters.

Before enabling a PostgreSQL adapter, add tests asserting:
- the returned ID equals the database's canonical ID, not the caller's proposed ID;
- repeated create with the same owner-scoped client key and identical digest returns exactly the same ID;
- same key with a different digest conflicts;
- tenant/owner changes produce distinct identity scopes and cannot read each other's plans;
- all later reservation and outbox operations use the persisted ID.

The adapter contract should explicitly state that `create_plan` may canonicalize the proposed ID and that callers must use the returned record. If this behavior is unacceptable for existing consumers, introduce a versioned ID migration rather than silently aliasing two IDs.
