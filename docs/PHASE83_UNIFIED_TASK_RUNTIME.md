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

### Explicit retry-safety opt-in in the legacy goal engine

`DurableTaskEngine.submit()` now accepts `retry_safe=False`. Both worker-reported failures and expired-lease recovery consume another attempt only when the submitter explicitly sets `retry_safe=True` and the configured attempt budget remains. This is deliberately conservative: a worker can lose its lease or report an error after an external side effect partially completed, so a retry budget alone is not proof that replay is safe. The flag is included in the submission fingerprint, so reusing an idempotency key while changing retry safety is rejected. Existing SQLite databases receive an additive `retry_safe` column with a false default; existing tasks therefore do not silently become replayable. Callers must only opt in when the task operation is idempotent or protected by a stable downstream idempotency key. The separate `TaskRuntime` retry contract still needs to converge on this explicit policy before the two engines are unified.


- Task creation is idempotent by caller-supplied key.
- State updates and event appends use optimistic version checks.
- Failed tasks are terminal records. `TaskRuntime.retry_failed()` creates a new run with a fresh idempotency key and approval state; repositories do not reopen failed records in place. This preserves provenance and prevents late progress/events from mutating a failed execution.
- A worker must own an active lease to complete a task.
- Expired leases must be marked interrupted by the existing store; side-effecting tasks are not automatically replayed.
- Cancellation updates lifecycle state. A future worker/adapter must observe cancellation and safely stop long-running operations.
- Task events record progress and artifacts as metadata; references do not prove an artifact's content or integrity.

## Cancellation safety invariant

A task in the `running` state cannot be terminally cancelled while its worker may still be performing an external side effect. The caller receives a transition conflict and the live lease remains valid; the worker must report its actual outcome. This matches the legacy `DurableTaskEngine` safety rule and avoids presenting a database state change as proof that an external operation stopped. Queued and other transition-eligible non-running tasks remain cancellable. A future cooperative cancellation protocol must add an explicit cancellation request signal, worker acknowledgement, lease fencing, timeout/recovery behavior, and audit events before allowing cancellation of active work.

Both SQLite and PostgreSQL `WorkflowRepository` adapters enforce this through the shared `TaskRuntime.cancel()` guard. Regression coverage asserts that a running task remains running with its lease intact after a cancellation attempt, then can still complete under the current worker lease.

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
- Current lifecycle split is explicit: legacy `DurableTaskEngine` owns goal/task dependency edges, while `TaskRuntime` and its SQLite/PostgreSQL `WorkflowRepository` own individual workflow-run lifecycle and task events. These are not yet one canonical goal/task model; do not claim that PostgreSQL workflow persistence has migrated legacy goals or dependency edges.
- Goal aggregation must distinguish lifecycle settlement from execution outcome. The legacy goal state `completed` currently means every child task is terminal (including failed/cancelled children), not that every task succeeded. The canonical model must expose a separate bounded aggregate outcome (for example succeeded, failed, cancelled, or mixed) and define precedence for mixed child outcomes before migration. Preserve the existing state meaning until that contract and compatibility migration are implemented.
- The legacy SQLite goal store now persists a nullable `goal_outcome` independently of `state`. Once all non-empty child task sets are terminal, outcomes are `succeeded` (all succeeded), `failed` (all failed), `cancelled` (all cancelled), or `mixed` (any combination). A cancelled goal computes the same aggregate after atomically cancelling its queued children. Existing `completed`/`cancelled` lifecycle semantics are unchanged; old databases receive an additive column and historical rows remain NULL because their child history is not rewritten. The PostgreSQL goal aggregate and canonical cross-backend repository contract remain future work.
- The legacy SQLite `DurableTaskEngine` now issues a cryptographically random opaque `lease_generation` on every claim. Heartbeat, completion, failure, and rejection require that exact generation as well as the worker ID and an unexpired lease; comparisons are constant-time. The generation is cleared atomically when the lease is released, recovered, or terminally transitioned. The worker runtime must retain and pass the generation returned by claim; worker IDs are labels, never fencing credentials.
- The generation is returned only in the successful `claim()` response; ordinary `get_task()` and idempotent `submit()` responses strip it so read paths do not disclose a bearer fencing credential. The claim response is captured inside the claim transaction, not re-read after commit.
- Lease renewal extends expiry and advances the optimistic task version but does not rotate the per-claim generation. A worker credential remains bound to the same claim for its full lease lifetime; a new claim receives a new generation. Expired-lease recovery clears the SQLite generation.
- Existing SQLite databases receive an additive nullable `lease_generation` column. Existing in-flight leases have no trustworthy generation and deliberately fail closed after upgrade; allow them to expire and be recovered before restarting workers. Do not backfill generations or infer them from worker IDs.
- The legacy `DurableTaskEngine` remains a local SQLite compatibility engine, not a distributed multi-host authority. Per-claim generations now fence stale workers even when a worker ID is reused; SQLite transactions serialize local claims. Use the PostgreSQL `TaskRuntime` for distributed workers and shared authoritative state.
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

### PostgreSQL plan adapter implementation gate (source and test inventory)

The existing PostgreSQL integration suite already exercises important distributed behavior with two independent repository instances: owner-scoped idempotency/replay, foreign-owner read isolation, conditional subtask updates, exclusive reservation, generation takeover, stale outbox claim fencing/recovery, expiry retirement, and cancellation tombstones. These are existing tests and design strengths; do not replace them with weaker adapter-only mocks.

The SQLite port and PostgreSQL implementation still differ at the plan aggregate boundary:
- The port accepts and returns `AgentPlanRecord`; PostgreSQL `create_plan` and `get_plan` return a mapping bundle containing a database row and subtask rows.
- PostgreSQL uses a client idempotency key plus explicit tenant/owner scope, while the SQLite record also carries a precomputed scoped idempotency key and proposed plan ID.
- PostgreSQL dispatch reservations carry expiry and a reservation token; the port value has no expiry field. A typed adapter must preserve expiry and generation rather than discard them.
- The coordinator currently constructs a SQLite repository internally, so constructor injection alone is not sufficient. Its plan identity, task runtime identity, and dispatch/outbox crash-recovery behavior must be addressed together.
- The PostgreSQL outbox methods (`claim_outbox`, `mark_enqueued`, `recover_stale_outbox`) are a delivery-worker API. Keep them separate from the coordinator's aggregate repository port.

Implementation order:
1. Define a typed PostgreSQL aggregate representation and an explicit conversion boundary for database rows/JSON fields. Validate every required column and reject malformed persisted data rather than silently defaulting security-relevant fields.
2. Define a versioned canonical plan-ID strategy and migration/compatibility policy. Preserve the database-generated ID on create and replay; do not return the coordinator's pre-persistence candidate ID.
3. Normalize reservations into a common value that retains tenant, owner, generation, claim token, state, task ID, and PostgreSQL expiry. Document the SQLite adapter's local-only lease semantics separately from PostgreSQL's distributed expiry semantics.
4. Add shared repository contract tests and typed-adapter tests, while retaining the existing PostgreSQL two-instance concurrency and outbox tests.
5. Add coordinator repository injection only after its plan/task identity and cross-store crash windows are reconciled. Keep the specialist-agent registry as a separate bounded context; do not silently turn it into task state.
6. Enable distributed composition only when PostgreSQL plan storage, PostgreSQL task runtime, strict tenant/owner identity, and outbox reconciliation are all selected and validated at startup.

No production wiring or distributed-readiness claim is justified by the current adapter alone.

### Typed PostgreSQL adapter: first implementation slice

Added `automation.postgres_agent_plan_adapter.PostgresAgentPlanRepositoryAdapter` as an explicit conversion boundary around the raw PostgreSQL repository. It implements the existing coordinator-facing repository shape while keeping outbox worker methods on the raw repository.

The adapter:
- converts PostgreSQL plan/subtask row bundles into `AgentPlanRecord` and `PlanSubtaskRecord`;
- returns the persisted canonical PostgreSQL plan ID on create and replay;
- enforces non-empty tenant and owner scope for every operation;
- rejects malformed persisted aggregate fields instead of silently defaulting permissions, capabilities, approval, status, or identity;
- preserves dispatch generation, reservation token, state, task binding, and expiry through the shared reservation value.

The shared `DispatchReservation` now has an optional `expires_at` field. SQLite remains compatible with `None`; PostgreSQL retains its database expiry timestamp.

Added focused adapter boundary tests for typed row conversion, canonical ID preservation, malformed persisted-data rejection, strict distributed owner scope, and expiry/generation preservation.

This is intentionally not production wiring. The adapter must still pass CI and PostgreSQL integration/contract tests, and the coordinator must be composed with the PostgreSQL TaskRuntime and crash-recovery/outbox worker before distributed orchestration is enabled.

### PostgreSQL adapter integration coverage

Extended the existing disposable-PostgreSQL integration suite to exercise the typed adapter against the real PostgreSQL repository, not only mocked row bundles. The new cases verify:
- create returns the database's canonical `ap_...` identifier rather than the caller's legacy proposal;
- idempotent replay returns the same canonical identifier;
- owner-scoped retrieval returns the same typed aggregate and hides it from another owner;
- a PostgreSQL reservation retains its active token, generation, state, and expiry through the adapter.

These cases use the existing `YUKI_TEST_POSTGRES_URL` integration environment and are skipped when it is not configured. They are authored but have not been executed in this environment; CI must report their result before this adapter can be considered validated.

### Coordinator repository composition seam

The specialist-agent coordinator now accepts an injected `AgentPlanRepository` implementation and validates the full lifecycle port at construction. The SQLite repository remains the default for compatibility; the coordinator's separate specialist registry remains SQLite-backed. This is dependency injection only—not a production switch to PostgreSQL.

This enables a later composition root to supply `PostgresAgentPlanRepositoryAdapter` while retaining the existing coordinator behavior, but distributed activation remains blocked on cross-store crash recovery and the specialist registry's multi-host persistence design. The coordinator and TaskRuntime must share the same tenant/owner authority, and stable TaskRuntime idempotency remains necessary across the enqueue/plan-binding failure window. Do not enable a mixed SQLite/PostgreSQL deployment by accident.

### Cross-store dispatch crash-recovery proof

Added a disposable-PostgreSQL integration scenario for the critical failure window: TaskRuntime commits a tenant-owned workflow task, then the coordinator process dies before binding that task ID to the plan. The test expires the first dispatch reservation, obtains a newer fenced generation from an independent repository connection, replays the exact stable TaskRuntime idempotency key, and verifies that the same task ID is bound to the plan exactly once.

This validates the intended recovery mechanism at the repository/runtime boundary; it does not make the two databases atomic. The plan outbox remains a durable dispatch intent and reservation fencing prevents stale generations from binding. A production worker still needs explicit reconciliation/claim ownership, retry/backoff, dead-letter policy, observability, and the specialist registry's multi-host persistence before distributed coordinator activation.


### PostgreSQL dispatch outbox retry policy

Hardened stale outbox recovery with bounded exponential retry scheduling and an explicit terminal-attempt threshold:

- A stale processing claim is invalidated and its claim token cleared before it can be reclaimed.
- Retry availability is delayed by exponential backoff (2, 4, 8 … seconds), capped at five minutes; the worker must only claim rows whose `available_at` has arrived.
- Once the configured maximum attempt count is reached, the row transitions to `dead_letter` rather than cycling indefinitely.
- Existing reservation-generation and per-claim token/generation fences remain required to finalize delivery; an old worker cannot complete a recovered claim.
- Added PostgreSQL integration coverage for delayed retry scheduling and dead-letter terminal behavior. The existing stale-claim test explicitly advances `available_at` after verifying the delay so it can deterministically exercise the next claim.

This is repository-level retry policy, not yet a continuously running production worker. Metrics, alerting, operator redrive controls, and a supervised worker lifecycle remain rollout requirements. Tests are authored but have not been run locally; GitHub Actions must validate the PostgreSQL integration cases.


### Fenced outbox claim heartbeat

Added `heartbeat_outbox_claim` to extend a long-running processing claim without weakening delivery ownership:

- The update requires the exact tenant, owner, plan, subtask, reservation token/generation, and outbox claim token/generation.
- The reservation must still be `reserved` and unexpired according to PostgreSQL's clock.
- Only a currently processing row with the matching claim fence can refresh its heartbeat timestamp; stale or superseded workers receive `False` and cannot revive their lease.
- Added a two-repository PostgreSQL integration test covering successful heartbeat, stale-claim recovery, rejection of the old claim, and acceptance of the replacement claim.

This is the persistence primitive needed by a supervised worker before it performs slow external task enqueue operations. A worker must heartbeat while processing and must still handle reservation expiry or claim loss as a cancellation/fencing event. The integration test is authored but not run locally; CI remains the validation gate.


### Supervised worker activation gate — payload and registry ownership

A code-path review of `SpecialistAgentCoordinator.advance`, the PostgreSQL outbox schema, and `TaskRuntime.enqueue` found that the supervised outbox worker must not yet be enabled. The current outbox row identifies the tenant/owner/plan/subtask and dispatch generation, but it is not a complete immutable execution envelope. In particular:

- The specialist action and enablement are currently resolved from the coordinator's SQLite-backed specialist registry. Two hosts can therefore observe different agent definitions or authorization state.
- `advance` resolves dependency completion and gathers bounded prior-task output before constructing the TaskRuntime step. The outbox does not persist that resolved, versioned execution input.
- TaskRuntime enqueue requires a trusted actor identity and a stable idempotency key. A worker must derive both from the same tenant/owner authority as the plan and workflow stores; it must not invent or elevate an actor from outbox contents.
- A worker that simply reads a subtask and dispatches it would bypass the coordinator's dependency readiness, specialist enablement, and permission checks.

Therefore the next implementation must establish a versioned, immutable dispatch envelope and a shared PostgreSQL-backed specialist registry (or an equivalent authoritative registry service) before a standalone worker can safely dispatch. The envelope must bind tenant, owner, plan/subtask, reservation generation, stable idempotency key, agent/action revision, required permissions, approval requirement, dependency snapshot/output digests, and a bounded argument payload. The worker must revalidate the current authorization and reservation fence immediately before enqueue, and use the same tenant/owner identity when creating the canonical TaskRuntime record.

Do not implement a worker that reconstructs execution authority from partial outbox fields or reads a host-local SQLite registry. This is a deliberate fail-closed gate, not a missing retry loop. The repository-level claim, heartbeat, recovery, backoff, and dead-letter primitives remain useful groundwork, but distributed dispatch is not production-ready until this contract, registry authority, and cross-store recovery are tested together with independent connections/processes.


### Dispatch-time specialist manifest revalidation

Hardened coordinator dispatch against specialist registry drift between plan creation and execution. Before reserving/enqueueing a subtask, the coordinator now reloads the registry and verifies that the specialist is still enabled and still satisfies the plan's persisted action, required capabilities, and required permissions. A changed or narrowed manifest fails the subtask closed without creating a TaskRuntime task. This prevents a plan from silently dispatching through a specialist whose registered authority no longer matches the plan that was approved.

Added regression coverage for capability removal and permission removal between plan submission and dispatch. This is a defensive check in the existing SQLite registry path; it does not make that registry a shared multi-host authority, and the PostgreSQL registry plus immutable execution envelope remain prerequisites for distributed workers. Tests are authored but have not been run locally; GitHub Actions is the validation gate.


### Specialist registry persistence port

Extracted specialist-manifest persistence from coordinator-owned SQL into `automation.specialist_agent_registry`. The coordinator now depends on a narrow `SpecialistAgentRegistry` port (`initialize`, `list_agents`, `register_agent`, `set_agent_enabled`) and validates injected adapters at composition time. The default `SQLiteSpecialistAgentRegistry` preserves the existing local/single-host behavior and schema; the registry remains separate from the agent-plan repository and canonical TaskRuntime store.

This is an adapter seam, not a PostgreSQL implementation or distributed-readiness claim. The next adapter must preserve atomic registration/enablement, validated manifest reads, cross-connection visibility, and concurrency semantics. Registry mutations remain behind coordinator authorization; adapters only persist routing metadata and never execute specialist code. Added tests for injected registry behavior and rejection of incomplete adapters. Tests are authored but not executed locally; CI remains the validation gate.


### PostgreSQL specialist registry adapter

Added `migrations/security/0017_specialist_agent_registry.sql` and `PostgresSpecialistAgentRegistry`. The registry is a global platform-level manifest authority, separate from tenant-owned agent plans and canonical workflow/task state. It stores only bounded routing metadata (agent ID, name, capabilities, allow-listed action, permissions, enabled state), not executable code, credentials, tool grants, or task payloads.

The adapter requires the migration to be applied before startup and fails closed when required columns or constraints are absent; it never performs runtime DDL. Initialization inserts missing bundled defaults only and does not overwrite operator-managed rows. Registration relies on the database primary key, and enable/disable changes use a single conditional `UPDATE ... RETURNING` with a database-managed manifest revision and clock. Repeating the same enabled state is idempotent. Independent PostgreSQL pools observe shared changes. Integration tests cover cross-pool visibility, concurrent enable/disable updates across independent pools, idempotent revision increments, duplicate IDs, non-overwriting initialization, unknown IDs, and invalid manifests. Tests are authored but not executed locally; CI and a disposable PostgreSQL integration service remain required validation gates.

This adapter can be injected into the coordinator, but distributed specialist execution is not thereby production-ready: the coordinator still needs a complete shared composition root, immutable execution envelopes, shared authorization/registry revision checks at dispatch time, and end-to-end multi-instance recovery tests. The SQLite adapter remains the default local path.


### PostgreSQL registry readiness and endpoint identity

The PostgreSQL specialist registry now exposes `check_ready() -> True` only after revalidating its required columns and constraints, so host composition can include it in protected-service readiness checks. It also exposes the credential-independent `database_identity` digest using the same endpoint helper as workflow and delegation stores; this permits a composition root to verify that separately pooled registry credentials target the intended PostgreSQL endpoint without retaining or comparing passwords. The digest is endpoint comparison metadata, not cryptographic proof of cluster identity; deployment DNS/network/TLS pinning remains required. The adapter still owns and closes only its own pool.


### PostgreSQL plan repository composition contract

`PostgresAgentPlanRepository` now exposes the same credential-independent `database_identity` comparison used by the workflow store, delegation authority, security lease store, and specialist registry. Its `check_ready()` method returns `True` only after verifying required plan, reservation, and outbox columns, constraints, and indexes; constructor startup and subsequent readiness probes use the same validation path. This gives a future production composition root a fail-closed way to verify schema readiness and database endpoint alignment before dispatch is enabled.

This does not yet activate PostgreSQL plans in the production worker: the production worker factory still composes canonical workflow/task execution and delegation only. The typed plan adapter/coordinator boundary and registry must be wired through a dedicated composition layer, and the outbox still lacks a complete immutable execution envelope and supervised dispatcher. No distributed specialist execution readiness is claimed.


### PostgreSQL-backed production specialist composition

Added `create_production_specialist_runtime()` as an explicit composition entry point layered on the existing production worker runtime. It creates the PostgreSQL plan repository, typed plan adapter, and PostgreSQL specialist manifest registry, then injects both into `SpecialistAgentCoordinator` alongside the existing TaskRuntime, central authorization gateway, actor resolver, trusted specialist workload-principal resolver, and policy revision. It uses an inert `:memory:` compatibility path only because the coordinator constructor still requires a path; the injected PostgreSQL adapters own all plan and registry persistence, so this composition does not open a local SQLite database.

The composed runtime revalidates worker/security readiness plus plan and registry schema readiness, and rejects any component whose endpoint identity differs from the canonical workflow store. Shutdown closes only the resources it owns and is idempotent. The caller remains responsible for supplying reviewed initial agent definitions and a trusted workload-principal resolver. This is a control-plane composition milestone, not proof of distributed specialist dispatch: immutable execution envelopes, a supervised outbox dispatcher, cross-store crash recovery, and multi-host integration CI remain outstanding. No worker loop is started by construction.


The composed specialist runtime now exposes `run_cycle()` as the readiness-gated operational entry point. Each invocation rechecks worker/security, workflow/delegation, plan repository, and specialist registry readiness and endpoint alignment before forwarding exactly one cycle to the existing supervised worker. It does not create a background thread or bypass the worker supervisor. A focused unit test covers forwarding and rejection after shutdown.


### Versioned immutable dispatch-envelope storage

Added migration `0018_agent_plan_dispatch_envelope.sql` and a fenced repository write boundary for the specialist outbox. A dispatch envelope is version 1, canonicalized as bounded JSON, and SHA-256 digested before it can be attached. The persisted envelope binds tenant, owner, plan, subtask, reservation generation, stable idempotency key, agent/action revision, required capabilities and permissions, approval requirement, dependency snapshot/output digests, and bounded argument input.

Envelope attachment is conditional on the exact live reservation token/generation, a pending outbox row, and the absence of a prior envelope. The database constraint requires the version, JSON payload, and digest to be present together and bounds the JSON size. Outbox claiming now excludes rows without a complete envelope, so legacy/incomplete intents cannot reach a worker accidentally.

This is the persistence contract only. The coordinator does not yet construct the complete envelope, the worker does not yet revalidate the registry revision and authorization against it, and no supervised dispatcher is enabled. Existing PostgreSQL integration tests and new envelope-specific tests must be updated to apply migration 0018 and attach valid envelopes before claim. Distributed dispatch remains disabled until those steps and cross-instance recovery validation pass.


### Coordinator-side envelope construction

The specialist coordinator now constructs the version-1 envelope from the persisted subtask, current versioned registry revision, current reservation generation, dependency task snapshot, dependency output digests, approval flag, and the exact bounded argument assembled for TaskRuntime. When the injected plan repository exposes the PostgreSQL envelope port, the coordinator persists this envelope before enqueueing the canonical task. Missing registry revision support or an invalid revision fails closed. The typed PostgreSQL plan adapter forwards the envelope write to the authoritative repository.

A coordinator unit test verifies envelope fields and that the envelope persistence hook is invoked before the task is created. The local SQLite adapter remains unchanged and does not pretend to provide distributed outbox guarantees. This still does not activate an outbox worker: a future worker must independently verify the stored digest, current manifest revision, permissions/approval and reservation/claim fences before it can execute.


### Dispatch envelope integrity verification at claim boundary

The PostgreSQL outbox claim path now canonicalizes each claimed JSON envelope and verifies its SHA-256 digest using constant-time digest comparison before returning any row to a worker caller. Invalid, malformed, oversized, or digest-mismatched envelopes are transitioned to `dead_letter` with their claim token cleared and are omitted from the returned batch. This is an integrity gate at the repository boundary, not a substitute for worker-side authorization, current registry-revision checks, approval enforcement, and reservation/claim heartbeat fencing. Added a PostgreSQL integration test that tampers with a persisted digest and verifies the row is dead-lettered without being handed off. The disposable-PostgreSQL fixture now applies migration 0018 before these tests.


### Nested dependency-envelope validation

Tightened version-1 envelope validation beyond the outer JSON size bound. Every dependency snapshot entry must now have exactly the expected subtask/task/state fields, use a unique bounded identifier, and represent a succeeded dependency. Output digest entries must reference a dependency in that snapshot and contain a lowercase SHA-256 digest. The argument rejects NUL bytes as well as oversized content. PostgreSQL integration coverage exercises malformed state, extra fields, orphan digest references, and invalid digest values.

The coordinator now builds dependency states by subtask identity rather than relying on parallel-list positional indexing. This keeps the immutable snapshot aligned with the actual dependency even if traversal or collection logic changes.


### Dual-fenced worker completion primitive

Added `complete_claimed_dispatch` for the future independent outbox worker path. Unlike the coordinator's compatibility completion method, this API requires both the live reservation token/generation and the current outbox claim token/generation. In one PostgreSQL transaction it advances the reservation, marks the exact processing claim delivered, and binds the canonical task ID/status to the matching subtask generation. A stale or replayed claim cannot complete delivery; a partial failure rolls the transaction back. A PostgreSQL integration test covers successful completion and replay rejection.

This is a repository primitive only. No worker loop is enabled, and a worker must still validate authorization, approval, manifest revision, dependencies, and idempotent TaskRuntime enqueue before calling it.


### Typed adapter boundary for worker fencing operations

Exposed the raw PostgreSQL outbox worker operations through the typed plan adapter without adding them to the coordinator lifecycle port: bounded outbox claim, claim heartbeat, and dual-fenced completion. Every operation preserves explicit tenant/owner scope; heartbeat and completion forward both reservation and claim generations unchanged. Added a PostgreSQL integration test that exercises heartbeat and completion through the adapter, proving the future worker can use the same typed persistence boundary without reaching around it to the raw repository.

The adapter is not itself a dispatcher. It does not resolve actor identity, authorize actions, check approval, validate current registry revisions, enqueue TaskRuntime work, or supervise retries. Those responsibilities remain mandatory before production worker activation.


### Typed immutable outbox claim records

The PostgreSQL plan adapter no longer exposes raw dictionaries as its worker claim contract. It returns frozen `ClaimedAgentPlanDispatch` records containing explicit tenant/owner/plan/subtask scope, reservation and claim tokens/generations, envelope version/digest, and the immutable envelope. Before constructing a record, the adapter checks the exact version-1 envelope key set, verifies envelope scope and dispatch generation against the database row, and validates the presence and basic shape of both fencing credentials. The repository remains responsible for canonical digest verification before handoff.

This narrows the future worker interface and makes it harder to accidentally trust tenant/owner identity supplied only inside an untrusted argument. It does not authorize the action: the worker must still resolve the actor from trusted state and independently enforce permissions, approval, registry revision, and dependency provenance.


### Atomic dispatch heartbeat and recursively immutable claims

The worker-facing claim record now recursively freezes its JSON envelope: objects become read-only mappings and arrays become tuples. This closes the mutation gap left by a shallow frozen dataclass. The adapter validates the claim structure before exposing it and carries the lease TTL explicitly.

The PostgreSQL heartbeat now extends both the reservation expiry and the processing outbox heartbeat timestamp in one transaction, guarded by the current reservation token/generation and claim token/generation. If the outbox claim fence is lost after the reservation update, the transaction raises and rolls back the reservation extension; stale workers cannot keep a reservation alive. TTL is bounded to 5–900 seconds. Integration coverage asserts nested envelope mutation is rejected and a valid heartbeat advances the reservation expiry.

This is still a persistence primitive, not an active dispatcher. A production worker must heartbeat at a cadence comfortably shorter than its TTL and stop immediately when heartbeat returns false or raises a fencing conflict.

### Enqueued-state invariant for worker completion

The dual-fenced worker completion API now accepts only the `queued` subtask state. A successful outbox handoff means the canonical TaskRuntime task has been durably enqueued; it must not let the dispatcher mark work as succeeded, failed, or otherwise terminal. Those states belong to subsequent task execution and reconciliation. A PostgreSQL integration test verifies invalid terminal-state input is rejected before either fencing record or subtask state is mutated.

### Production coordinator dispatch activation gate

The production specialist composition now constructs its coordinator with direct dispatch explicitly disabled. The coordinator's legacy `advance()` path otherwise enqueues directly through TaskRuntime after writing the immutable outbox envelope, which would allow the coordinator and a future independent outbox worker to race and create duplicate delivery attempts. Production plan submission and inspection remain available, but `advance()` fails closed until the supervised outbox worker is implemented and activated through an explicit deployment-owned composition decision. Local/test coordinators retain the existing enabled default for compatibility. Regression tests verify the gate rejects dispatch before task creation and does not mutate the pending subtask.


### Supervised outbox worker activation contract (implementation gate)

The outbox worker must be a separate finite-cycle service, not a second call path from the coordinator. Its production implementation is not present yet. The following sequence is mandatory for every claimed record:

1. **Claim and fence.** Claim a bounded batch through `PostgresAgentPlanRepositoryAdapter.claim_outbox()`. Treat the immutable envelope as untrusted persisted input despite its digest. Retain both reservation and outbox claim tokens/generations. Never log either token or the full argument.
2. **Resolve owner from trusted state.** Resolve the human owner using the deployment-owned principal resolver. Require a human principal whose principal ID and tenant exactly match the persisted outbox scope. Do not construct a principal from envelope fields, accept model output as identity, or impersonate the service actor as the owner.
3. **Re-authorize at delivery time.** Re-evaluate the current central policy for `agent:dispatch` against the exact plan resource and current policy revision. Fail closed on unavailable policy, mismatched revision, missing principal, or denied decision. Enqueue under the verified owner context so TaskRuntime's own task-create authorization and ownership binding also run. The dispatcher must not use a broad service-actor bypass.
4. **Revalidate the specialist manifest.** Read the current registry record and its monotonic manifest revision. Require the revision to equal the envelope revision, the agent to remain enabled, the action to match exactly, and the current capabilities and permissions to contain the recorded requirements. Resolve the specialist workload principal from the trusted registry/deployment resolver; never accept a principal or executable handler from the envelope.
5. **Revalidate approval.** Preserve `requires_approval` exactly when creating the canonical TaskRuntime task. A true value must create a task subject to the existing approval gate; the dispatcher must never approve it, downgrade the flag, or execute the specialist itself. Missing or malformed approval metadata is a permanent validation failure.
6. **Revalidate dependency provenance.** Load every dependency task through the owner-scoped TaskRuntime API. Require the exact task ID and `succeeded` state captured in the snapshot. For every supplied output digest, read the canonical result, extract the same bounded output field, canonicalize it exactly as the coordinator did, and compare SHA-256 digests in constant time. Missing tasks, changed states, missing outputs, or digest mismatches fail closed; never substitute a newer task or output.
7. **Enqueue idempotently.** Call TaskRuntime with the immutable stable idempotency key from the envelope, exact agent action, bounded argument, and approval flag. Require the returned task to be owned by the same tenant and human principal. The stable key is the cross-store crash-recovery mechanism; never generate a new key per attempt.
8. **Complete under both fences.** Only after TaskRuntime confirms durable enqueue, call `complete_claimed_dispatch()` with the exact reservation token/generation and claim token/generation and status `queued`. A false result or fencing conflict means this worker lost ownership; do not mutate plan state or attempt a second completion with refreshed tokens.
9. **Supervise long operations.** Heartbeat at a configured interval materially shorter than the bounded lease TTL. Stop work immediately on a false heartbeat or fencing conflict. A worker that loses either fence must not enqueue further side effects. Recovery may requeue only after the processing heartbeat is stale; stable TaskRuntime idempotency handles the crash window where enqueue committed before dual-fenced completion.
10. **Classify failures and observe.** Separate permanent validation/authorization failures from transient infrastructure failures. Permanent failures must be moved to a bounded dead-letter path with a redacted reason and operator-visible audit event; transient failures use bounded retry/backoff. Emit structured audit/metrics for claim, authorization, manifest revision, dependency verification, enqueue, heartbeat loss, completion, retry, and dead-letter without credentials, tokens, or task arguments.

**Activation blockers:** the current version-1 envelope contains the owner and policy-relevant task inputs but does not itself constitute an authorization grant. A production worker still needs a trusted owner-principal resolver, current-policy authorization, a revisioned registry read, an owner-scoped dependency/result reader, a supervised retry/dead-letter loop, and an auditable permanent-failure transition. The production composition must continue to keep coordinator direct dispatch disabled until these interfaces are implemented and real PostgreSQL multi-connection crash/replay tests cover the worker end to end. The existing task worker supervisor is not a substitute: it executes already-enqueued tasks and does not deliver specialist-plan outbox records.


#

### Dispatcher enqueue authorization and dependency contract

The outbox dispatcher must treat dispatch authorization and task creation as two
independent enforcement points. Before creating a TaskRuntime record it must:

- Resolve the exact tenant-owned human principal through the deployment-owned
  identity resolver; never construct a principal from the outbox envelope.
- Authorize `agent:dispatch` against the exact `agent-plan:{plan_id}` resource
  using the current policy revision and the audited central gateway.
- Revalidate the specialist manifest from the authoritative registry, including
  enabled state, exact action, manifest revision, required capabilities, and
  required permissions. The PostgreSQL registry currently exposes a complete
  `list_agents()` snapshot and per-agent `get_manifest_revision()`; a future
  point lookup may be added without changing the dispatch contract.
- Enqueue through the ordinary `TaskRuntime.enqueue()` API as the resolved
  human owner. That API performs its own `task:create` authorization and derives
  tenant ownership from the authenticated human principal. The dispatcher must
  not weaken, skip, or replace this check with a service actor.
- Preserve the envelope's stable idempotency key, exact action and bounded
  argument, and `requires_approval` flag. A dispatch worker must never approve
  a task or manufacture an approval record.
- Re-read every dependency task under the same owner and verify it is still
  succeeded before enqueue. Compare each current output digest with the
  immutable envelope digest; missing, changed, or foreign-owner outputs block
  dispatch rather than silently using stale context.
- Treat enqueue as at-least-once. If the process crashes after TaskRuntime
  commits but before outbox completion, retry must recover the same task by
  stable idempotency key and owner-scoped read authorization, then complete
  only under both current PostgreSQL fences.
- Heartbeat both leases while work is in flight. If heartbeat returns false or
  loses either fence, stop before any further side effect and never complete the
  stale claim. Permanent identity, authorization, manifest, approval, or
  dependency failures require a fenced dead-letter transition; transient
  infrastructure failures require bounded retry/backoff.

The existing security helper and PostgreSQL primitives are necessary but are
not a dispatcher. Production specialist dispatch remains disabled until the
worker, its fenced failure/retry path, crash-window recovery, and cross-connection
integration tests are implemented and CI-verified.

## Reusable trusted identity and dispatch authorization gates

Added `automation.specialist_dispatch_security` as a small, side-effect-free security boundary for the future dispatcher. Owner resolution accepts only a deployment-supplied resolver result that is a human principal with the exact persisted principal ID and tenant, and rejects service/workload identities or principals carrying an agent identity. Specialist workload resolution independently requires a workload principal with the exact tenant and agent ID. Neither helper constructs a principal from envelope data.

The dispatch authorization helper sends a fresh high-risk `agent:dispatch` request for the exact `agent-plan:{plan_id}` resource through the central `AuthorizationGateway`, pins the expected policy revision, and rejects denial, stale policy, malformed results, or audit failure. Because the gateway durably audits before returning, no enqueue may follow an unaudited allow.

Focused unit tests cover trusted resolver failures, tenant/subject/kind/agent mismatches, exact resource/action/risk/revision, stale policy, explicit denial, and audit outage. These are reusable gates only; they do not claim that an outbox dispatcher, dependency reader, retry supervisor, or end-to-end delivery path has been implemented. Production dispatch remains disabled.


The authorization helper also takes the expected tenant explicitly and compares it to the resolved human principal before calling policy. This prevents callers from accidentally skipping the owner-resolution helper and authorizing a human principal with a missing or cross-tenant binding.


### Dual-fenced permanent dispatch failure primitive

The PostgreSQL outbox now has a bounded `failure_code` classification column
and a worker-facing `dead_letter_claimed_dispatch()` transition. It requires
the current reservation token/generation and outbox claim token/generation,
then atomically marks the reservation abandoned, dead-letters the outbox row,
clears the claim token, and marks the matching non-terminal subtask failed.
Only stable lowercase classification codes are persisted; exception messages,
credentials, and arbitrary payloads are rejected. Readiness requires the new
column and check constraint. Disposable-PostgreSQL integration tests cover
success, replay rejection, state atomicity, and invalid classification.

This is a persistence primitive, not a complete dispatcher. The worker must
still decide which failures are permanent, emit the required redacted audit
event before/with the transition, and ensure a lost fence stops all further
side effects. Transient failures continue through the existing bounded stale
claim recovery/backoff policy.


### Atomic specialist-task visibility gate

A dispatcher must not expose a newly-created task to ordinary workers before
its outbox delivery is durably completed. The workflow store now supports a
`dispatch_ready` gate (default `true` for all existing task creation). A
dispatcher can enqueue an owner-scoped task with `dispatch_ready=false`;
PostgreSQL and SQLite claim paths exclude held tasks. The stable idempotency
key preserves the held state on retries and rejects attempts to reuse the key
with a different visibility state.

Migration 0020 adds the PostgreSQL column, constraint, and ready-task index.
The plan repository's `complete_claimed_dispatch()` now requires the matching
tenant-owned workflow row to still be queued and held. In the same transaction
as both fencing checks, outbox delivery, and subtask binding, it flips
`dispatch_ready=true`. If the process crashes after task creation but before
outbox completion, the task remains invisible; a retry can recover the same
task by idempotency key and complete the dual-fenced transaction. If any fence
or task ownership/state check fails, the transaction rolls back and the task
remains held.

This closes the enqueue-versus-worker-claim race without weakening the
queued-only completion invariant. It depends on the plan repository and
workflow repository sharing the same physical PostgreSQL database, which the
production composition already verifies. End-to-end dispatcher activation
still requires owner authorization, registry and dependency revalidation,
failure auditing, heartbeat handling, and crash/replay integration tests.


The visibility bit is now carried through the typed `WorkflowRun` and
`TaskRecord` models as `dispatch_ready`. This lets a dispatcher recover by
stable idempotency key and explicitly prove that the recovered task remains
held before it attempts the dual-fenced outbox completion. Both workflow-store
implementations advertise the visibility capability; TaskRuntime refuses a
held enqueue if its configured repository cannot enforce the gate. SQLite and
PostgreSQL contract tests cover held-state persistence, non-claimability,
idempotency-state mismatch, and release.


### Finite-cycle PostgreSQL outbox dispatcher

`automation.postgres_agent_plan_dispatcher.PostgresAgentPlanOutboxDispatcher`
now provides a bounded, explicitly invoked dispatcher cycle. It is not started
by the production composition root. Its constructor requires the typed
PostgreSQL plan adapter, the same PostgreSQL-backed TaskRuntime and registry,
the exact central authorization gateway used by TaskRuntime, trusted human and
workload identity resolvers, a policy revision, and a deployment-owned durable
redacted audit writer.

For each claim it renews both leases, validates the immutable envelope and
scope, resolves (never constructs) the human owner and specialist workload
principal, requires fresh high-risk `agent:dispatch` authorization for the
exact plan, revalidates the current enabled manifest and revision, and
re-reads each dependency task under the human owner. Dependency output is normalized exactly as the coordinator does (replace NUL
with a space, then strip surrounding whitespace) before SHA-256 verification. Only the supported `emit` action is accepted.

The dispatcher first looks up the stable idempotency key under the owner. A
new task is enqueued through the ordinary TaskRuntime as that human owner with
the persisted approval requirement and `dispatch_ready=false`. A recovered
task must still be queued, held, and match the exact action/argument/approval
fields. The dispatcher then heartbeats both leases and writes a redacted
`agent_dispatch.task_held` audit event before calling the dual-fenced
completion transaction, which atomically binds the task and releases it to
ordinary workers.

The audit writer must be durable and enforce idempotency on the supplied
`event_idempotency_key`. The dispatcher derives this SHA-256 key from a canonical compact JSON array
containing event type, tenant, owner, plan, subtask, and dispatch generation;
this unambiguous encoding prevents delimiter-based identity collisions. It
deliberately excludes the renewable outbox claim generation so a retry after
lease expiry addresses the same logical event. The writer also receives claim generation for forensic
context, but must not use it to create a new logical event. It receives only
identifiers and bounded classifications; it must never receive task arguments,
dependency output, envelope contents, credentials, or exception strings.
Audit failure prevents the corresponding state transition. Permanent
identity/policy/envelope/dependency failures are audited before the dual-fenced
dead-letter transaction. Infrastructure and audit failures are left for the
existing stale-claim recovery and bounded backoff.

The dispatcher exposes only `run_cycle()`; it has no hidden loop or automatic
thread. Production dispatch remains disabled until the deployment wires a
durable audit writer, adds full PostgreSQL integration coverage for crash
windows and authorization/registry changes, and observes green protected CI.


Manifest validation uses the registry's new `get_manifest_snapshot()` so the
manifest object and its revision are read from one PostgreSQL row snapshot,
rather than separate list/revision reads. The dispatcher also rejects any
action other than the worker's supported `emit` action and verifies the
workload principal carries the required specialist permissions.

Stale-claim recovery is an explicit deployment-owned callback, not an
unreviewed side effect inside the dispatcher. The callback must durably audit
retry exhaustion before invoking the repository's bounded stale-claim
recovery; the dispatcher validates its bounded result before claiming new
work. This keeps recovery/dead-letter audit policy under the deployment's
durable audit boundary.


When stale-claim recovery exhausts its attempt budget, the PostgreSQL recovery
transaction now records the bounded `retry_exhausted` failure code, abandons
the matching reservation generation, and marks only that generation's pending
or reserved subtask failed. If the generation fence does not match, the
transaction does not mutate a newer subtask. This closes the former gap where
an outbox could become dead-lettered while its plan remained indefinitely
reserved. The deployment-owned recovery callback remains responsible for
durably auditing retry-exhaustion candidates before invoking that transaction.


The stale-recovery transaction locks the matching reservation generation
before its outbox row, then updates the subtask, preserving the same
reservation → outbox → subtask lock order used by heartbeat, completion and
fenced dead-letter operations. Retry-exhaustion finalization therefore avoids
introducing an inverse lock order into concurrent dispatch.


TaskRuntime authorization denials during idempotency recovery or held-task
creation are classified as bounded permanent policy failures and audited before
the fenced dead-letter transition. They are not retried indefinitely as if
they were transient database or identity-provider outages.

### Shared goal outcome domain rule

Goal outcome aggregation now lives in the provider-neutral lifecycle domain
module (`core.task_lifecycle.aggregate_goal_outcome`) rather than being
implemented inside the SQLite queue adapter. The SQLite engine consumes this
rule, and its regression suite exercises the pure rule across all-success,
all-failure, all-cancelled, mixed terminal, incomplete, and empty inputs.
This is a narrow extraction: it does not claim that PostgreSQL goal storage,
dependency propagation, or goal lifecycle persistence has been implemented.
The existing `core.task_lifecycle` SQLite task store remains a separate
persistence adapter; only the pure aggregation rule is shared today.

Goal cancellation events now carry the same aggregate `outcome` field as
goal-completion events, alongside the count of queued tasks cancelled by the
operation. This makes the terminal goal event self-describing for event
consumers; a cancelled goal with previously failed children reports `mixed`
rather than implying that every child was cancelled.

### PostgreSQL distributed goal schema foundation

Migration `0021_distributed_workflow_goals.sql` introduces tenant-scoped
`workflow_goals`, `workflow_goal_tasks`, `workflow_goal_dependencies`, and
`workflow_goal_events`. Goal rows carry separate lifecycle state and aggregate
outcome; task links reference the existing PostgreSQL workflow-run authority
and enforce tenant plus owner-principal alignment. Dependency edges are
constrained to tasks within the same tenant and goal. Goal events are
append-oriented records with bounded JSON object details and a cursor index.

The schema is now backed by an initial PostgreSQL goal repository and an
atomic terminal-transition outcome hook. Dependency readiness/blocked
propagation, goal cancellation semantics, and a shared SQLite/PostgreSQL
goal-engine contract remain incomplete; this is not yet a complete distributed
goal orchestrator.

The migration also makes goal tenant/ID/owner identity immutable through a
database trigger and protects goal event history against UPDATE/DELETE. The
PostgreSQL integration suite asserts both triggers are installed. These
controls protect the schema boundary; they do not replace authenticated,
tenant-scoped repository queries or a future explicit audited transfer design.

The goal table also constrains lifecycle/outcome consistency: completed goals must have a terminal aggregate outcome, while active and paused goals must not expose one. Cancelled empty goals may retain a null outcome because they contain no child task outcomes to aggregate.

## PostgreSQL goal repository foundation (2026-11-25)

`automation.postgres_goal_store.PostgresWorkflowGoalStore` now provides a first tenant/owner-scoped persistence adapter over migration 0021. It can create goals, attach already-persisted workflow runs, add same-goal dependency edges, read scoped goal metadata/events, and refresh a goal's aggregate outcome. Every operation requires both tenant and owner principal; the composite foreign keys remain the final database-level ownership check. Goal graph edits lock the parent goal row before recursive cycle detection, serializing concurrent edge additions within one goal. Goal event history remains append-only.

The shared `aggregate_goal_outcome` rule now treats `interrupted` workflow runs as unsuccessful (`failed`) for aggregation, because the canonical workflow lifecycle marks interrupted runs terminal. A still-running, queued, or approval-waiting child keeps the aggregate unset; mixed terminal outcomes remain `mixed`.

**This is not yet a complete distributed goal runtime.** The goal repository does not execute workflow runs or propagate failed prerequisites into dependent task state, and explicit goal cancellation semantics are not yet implemented. Terminal workflow transitions now call `refresh_goal_outcome_for_task()` inside the same PostgreSQL transaction, including lease-expiry recovery to `interrupted`. The hook resolves membership using tenant and owner scope, locks the parent goal, aggregates the child states visible in that transaction, and appends the completion event before commit. If aggregation or event persistence fails, the child transition rolls back as well. Attaching an already-terminal workflow run also reconciles its goal inside the membership transaction. `refresh_outcome()` remains a repair/reconciliation operation. Initial PostgreSQL tests cover owner-scope rejection, idempotent membership, cycle rejection, append-only events, interrupted outcomes, and a terminal transition updating its goal. CI has not yet reported a result. Remaining coverage includes concurrent graph writers, cross-tenant task FK rejection, and rollback behavior when goal event persistence fails.

## Dependency-gated distributed execution (2026-11-25)

Migration 0022 adds `workflow_runs.dependency_ready`, deliberately separate from `dispatch_ready`. The latter remains the specialist/outbox visibility gate; the former is controlled only by the goal dependency graph. PostgreSQL worker claims require both flags, and direct queued-to-running transitions enforce both flags so callers cannot bypass either gate.

Adding an edge locks the dependent workflow row (so a claim or start cannot race past the new edge), validates both task scopes, serializes graph edits on the parent goal, and sets `dependency_ready=FALSE`. When a prerequisite succeeds, the transaction hook releases a queued dependent only when every prerequisite is `succeeded`. When a prerequisite fails, is cancelled, or is interrupted, queued dispatch-visible descendants are failed breadth-first with `dependency_failed` and ordinary append-only task events in the same transaction. Held outbox tasks are deliberately excluded so this hook cannot orphan their reservation/subtask state; they remain fail-closed pending a dedicated outbox-aware reconciliation path.

`claim_next()` reconciles both successful and failed terminal prerequisites before selecting work. This repairs the narrow edge-insertion/terminal-transition race where a transition hook may not yet see an uncommitted edge. Failed descendants are propagated with the same transactional event rules; successful prerequisites release a dependent only after all edges succeed. The claim query still requires `dispatch_ready=TRUE` independently, so reconciliation cannot expose a held specialist/outbox task.

Integration coverage now checks that dependent tasks cannot transition to running before prerequisite success, become eligible after all prerequisites succeed, and cannot use dependency release to bypass a false dispatch-visibility gate. A fault-injection trigger test proves that failure to append the goal completion event rolls back the child terminal transition and its task event. Migration manifest and fresh-database tests include version 0022. CI has not yet validated this commit.
## Goal cancellation semantics

`PostgresWorkflowGoalStore.cancel_goal()` now performs a tenant/owner-scoped, idempotent cancellation transaction. It rejects completed goals, transitions queued tasks whose dispatch visibility gate is open to `cancelled`, appends their ordinary `task.cancelled` events, marks the goal `cancelled`, computes the current aggregate outcome, and appends `goal.cancelled`. A repeated cancellation returns the existing cancelled goal without duplicating events.

Running tasks are deliberately not force-stopped because they may already be performing external side effects; their later terminal transition updates the cancelled goal's aggregate outcome. Queued tasks with `dispatch_ready=FALSE` remain held and are not mutated by this method; a dedicated outbox-aware cancellation protocol is still required to atomically reconcile reservations, outbox rows, plan subtasks, and workflow tasks. This boundary is explicit and fail-closed, not a claim that all specialist work is cancelled.