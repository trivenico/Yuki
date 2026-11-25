# Agent plan PostgreSQL cutover contract

Status: active design gate for WI-6U14-F. This document does not switch the coordinator to PostgreSQL.

## Current boundary

`SpecialistAgentCoordinator` still owns plan persistence directly through SQLite in `automation/agent_coordinator.py`. Its constructor creates/upgrades plan tables and lifecycle methods issue SQL through its SQLite connection. `PostgresAgentPlanRepository` is a separate persistence implementation; it is not yet a drop-in implementation of the SQLite `AgentPlanRepository` protocol.

There must be exactly one persistence authority for each plan. A partial cutover (for example, PostgreSQL for submit and SQLite for advance/cancel) creates split-brain state and is prohibited.

## Verified implementation state

- The SQLite protocol and adapter use a typed `AgentPlanRecord` for `create_plan` and `get_plan`.
- PostgreSQL `create_plan` accepts normalized keyword arguments and returns a dictionary containing plan/subtask data. PostgreSQL `get_plan` also returns a dictionary.
- Both implementations expose similarly named lifecycle methods, but matching names do not establish signature, return-type, transaction, or behavioral parity.
- PostgreSQL has typed `PlanSubtaskRecord` results for `list_subtasks`, owner-scoped conditional `update_subtask`, durable dispatch reservations, cancellation tombstones, and coordinator-facing `complete_dispatch`.
- PostgreSQL's worker-facing `mark_enqueued` validates both reservation fencing and outbox claim token/generation. The coordinator-facing `complete_dispatch` consumes the reservation token/generation and transitions a pending/processing outbox row to delivered, but does not require a particular worker claim token/generation. These are distinct completion contracts and must not be conflated.
- Migration `0015_agent_plan_dispatch_cancellation.sql` is applied explicitly by the PostgreSQL integration test setup. This review has not established an application startup migration runner or production migration execution path.
- A dedicated CI workflow was added in PR #1298 to run the SQLite and PostgreSQL repository test files against PostgreSQL 16.

## Coordinator lifecycle inventory

The repository boundary must support all of the following before a PostgreSQL-backed coordinator is enabled:

| Behavior | Current entry point | Required persistence behavior |
| --- | --- | --- |
| Create/idempotent submit | `submit` | Atomically create plan and subtasks; tenant/owner-scoped idempotency; canonical request digest |
| Read owned plan | `get_plan` | Tenant/owner/plan predicates; absent and foreign-owned plans indistinguishable |
| Dependency scheduling | `advance` | Consistent plan snapshot and conditional state transitions |
| Dispatch | `advance` | Atomic reservation/outbox intent; stable TaskRuntime idempotency key; owner verification before task binding |
| Task reconciliation | `advance` | Reconcile authoritative TaskRuntime state without terminal-state regression |
| Cancellation | `cancel_plan` | Fence pending dispatch; cancel actual tasks through TaskRuntime; preserve terminal outcomes |
| Subtask update | `_set_subtask` | Owner-scoped CAS and exact row-count checks |
| Delegation | `issue_workload_delegation` | Same owned-plan boundary; authorization remains in delegation/TaskRuntime services |
| Agent registry | `register_agent`, `set_agent_enabled`, `list_agents` | Separate concern; preserve cross-instance refresh and authorization |

## Non-negotiable invariants

1. **One authority per plan.** No fallback reads/writes to SQLite after PostgreSQL selection.
2. **Identity parity.** Resolve the principal through the central authorization gateway and require exact equality with TaskRuntime's resolved principal.
3. **Tenant predicates everywhere.** Reads, state changes, reservations, outbox claims, cancellation, and reconciliation carry tenant and owner scope.
4. **Task ownership binding.** A linked TaskRuntime row must be owned and match the plan tenant and owner exactly.
5. **Atomic plan creation.** Plan and subtasks commit together. The shared versioned canonical digest covers action, selected agent, capabilities, permissions, dependencies, and approval requirement.
6. **Dependency and approval semantics.** Preserve DAG validation, dependency gating, approval flags, and TaskRuntime approval flow.
7. **At-least-once dispatch only.** Enqueue is outside the database transaction; use stable idempotency keys and reconciliation. Do not claim exactly-once execution.
8. **Fencing on every asynchronous attempt.** Outbox claim token/generation must be checked by the worker finalization path; reservation token/generation alone is not a substitute.
9. **No terminal-state regression.** Late dispatch/cancellation/reconciliation must not overwrite succeeded, failed, or otherwise terminal runtime outcomes.
10. **Fail closed on schema mismatch.** Startup readiness validates the exact migration contract before serving requests.

## Remaining integration blockers

### Repository protocol is not implemented by both adapters

The current `AgentPlanRepository` protocol describes the SQLite typed-record API. PostgreSQL's `create_plan` and `get_plan` use different call signatures and dictionary return shapes. Some methods also have different keyword requirements and reservation record types. A test that merely invokes similarly named methods is not a meaningful contract-conformance test.

Next: define one versioned, typed port with explicit domain records and semantics. Then either adapt both backends to it or introduce explicit adapters. Keep compatibility wrappers where needed; do not silently change coordinator behavior. Add a structural/runtime conformance check plus shared behavioral scenarios against both implementations.

### Cancellation fencing must not own the task outcome

The coordinator's `cancel_plan` reads the current subtask state, persists dispatch cancellation, then reconciles the actual TaskRuntime task. It writes the final `cancelled`, `succeeded`, or `failed` status with an expected-status compare-and-set. The SQLite dispatch repository only fences dispatch and leaves the subtask status to the coordinator.

PostgreSQL previously set the subtask status to `cancelled` inside `cancel_dispatch`. That could make the coordinator's later compare-and-set fail after TaskRuntime reported a concurrent success or failure. The PostgreSQL adapter now updates the dispatch generation and tombstone without changing the subtask status; the coordinator remains the sole owner of the runtime outcome. A regression test asserts this boundary.

### Two PostgreSQL completion paths need a documented ownership boundary

`mark_enqueued` is the outbox-worker path and validates the outbox claim fence. `complete_dispatch` is a coordinator-facing atomic reservation completion path that does not validate a specific outbox claim. Before wiring either into the coordinator, specify which actor owns enqueue/finalization, whether it must hold an outbox claim, and which token/generation is authoritative. Add race tests proving a stale worker cannot finalize a newer claim and a stale coordinator cannot complete a superseded reservation.

### Migration execution path remains unverified

The integration test applies migrations `0007`, `0009`, and `0010`–`0015` explicitly. The new CI workflow provisions PostgreSQL and runs that test. This does not prove production startup or deployment automatically applies these migrations. Identify the operational migration mechanism, define ordering/rollback policy, and add a readiness test against the exact deployed schema before enabling PostgreSQL.

### Coordinator-level parity remains unproven

Repository tests do not replace coordinator-level tests for submit/idempotency, ownership isolation, DAG scheduling, approval waiting, cancellation races, task-owner mismatch, concurrent advances, crash recovery, and stale-claim fencing across two instances.

## Required implementation sequence

1. Define a typed repository port and explicit domain records; document method semantics and transaction boundaries.
2. Adapt SQLite to the port without changing existing behavior; move coordinator SQL behind the port while SQLite remains the only active backend.
3. Adapt PostgreSQL to the same port, including conditional state transitions, cancellation/reconciliation, and explicit outbox-worker versus coordinator completion contracts.
4. Run shared repository contract scenarios against both adapters, plus PostgreSQL concurrency/fencing tests.
5. Add coordinator-level PostgreSQL integration tests for submit, DAG/approval, cancellation, ownership, task binding, concurrent advance, and crash recovery.
6. Verify and document production migration execution and startup readiness. Never infer tenant/owner for legacy unowned plans.
7. Add explicit backend selection that fails startup if configuration/schema is incomplete; no silent SQLite fallback.
8. Consider cutover only after the above gates. Merge, deploy, and data migration remain separately authorized operations.

## Validation status

This contract reflects source review and open PR work. No production database migration was executed, no backend was switched, and no deployment occurred. PostgreSQL integration tests require `YUKI_TEST_POSTGRES_URL` and `psycopg`; CI results must be checked on the relevant PR head before merge.
