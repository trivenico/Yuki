# Agent plan PostgreSQL cutover contract

Status: design gate for WI-6U14-F. This document does not switch the coordinator to PostgreSQL.

## Current boundary

`SpecialistAgentCoordinator` still owns plan persistence directly through SQLite in `automation/agent_coordinator.py`. Its constructor creates and upgrades `agent_plans`, `agent_plan_subtasks`, and `specialist_agents`; lifecycle methods issue SQL through `_connect()`. `PostgresAgentPlanRepository` is a separate persistence primitive and is not yet a complete coordinator repository.

Do not configure both stores as active authorities for one plan. A partial cutover (for example, PostgreSQL for submit and SQLite for advance/cancel) creates split-brain state and is prohibited.

## Coordinator lifecycle inventory

The storage port must support all of the following before a PostgreSQL-backed coordinator is enabled:

| Coordinator behavior | Current entry point | Required persistence behavior |
| --- | --- | --- |
| Create/idempotent submit | `submit` | Atomically create plan and every subtask; scope idempotency by tenant and owner; reject same key with a different canonical request digest |
| Read owned plan | `get_plan` | Every read predicates on tenant, owner, and plan ID; absent and foreign-owned plans are indistinguishable |
| Dependency scheduling | `advance` | Read a consistent plan snapshot; persist blocked/failed/queued/succeeded state transitions with compare-and-set semantics |
| Dispatch | `advance` | Atomically reserve one eligible subtask and write an outbox intent; enqueue outside DB transaction with stable TaskRuntime idempotency key; verify resulting task owner before linking it |
| Task reconciliation | `advance` | Reconcile authoritative TaskRuntime state without regressing terminal state; task reads remain actor-authorized |
| Cancellation | `cancel_plan` | Persist pending-subtask cancellation and dispatch fencing; cancel dispatched tasks through TaskRuntime; reconcile races without overwriting terminal results |
| Subtask update | `_set_subtask` | Tenant/owner-scoped conditional update; check exact row count; reject stale generation/state updates |
| Delegation | `issue_workload_delegation` | Read plan through the same owned-plan boundary; delegation authority and active lease remain in TaskRuntime, not repository |
| Agent registry | `register_agent`, `set_agent_enabled`, `list_agents` | Separate concern; preserve cross-instance refresh and authorization. It must not be silently coupled to plan storage migration |

## Non-negotiable invariants

1. **One authority per plan.** A coordinator instance uses one repository for the complete plan lifecycle. No fallback reads/writes to SQLite after PostgreSQL selection.
2. **Identity parity.** Resolve the human principal through the central authorization gateway and require exact equality with TaskRuntime's resolved principal. Persist immutable tenant and owner identifiers.
3. **Tenant predicates everywhere.** Reads, state changes, reservations, outbox claims, cancellation, and reconciliation all carry tenant and owner scope. Never accept caller-supplied tenant IDs as authority.
4. **Task ownership binding.** A linked TaskRuntime row must be `owned` and match the plan's tenant and owner exactly. Legacy-unassigned tasks fail closed.
5. **Atomic plan creation.** Plan and all subtasks commit together. Idempotency is scoped to tenant + owner; the digest covers every behavior-affecting field, including selected agent/action, capabilities, permissions, dependency edges, approval requirement, and a versioned canonicalization format.
6. **Dependency and approval semantics.** Preserve the existing DAG validation, dependency gating, approval flags, and TaskRuntime approval flow. A repository must not independently authorize or bypass approvals.
7. **At-least-once dispatch only.** Reservation/outbox transactions are atomic, but provider/task enqueue is outside the transaction. Use stable TaskRuntime idempotency keys and explicit reconciliation; do not claim exactly-once execution.
8. **Fencing on every asynchronous attempt.** Reservation generation/token alone is insufficient if an outbox processing lease is recovered and reclaimed within the same reservation generation. Each claim attempt needs its own unguessable claim token or monotonically increasing claim generation, and finalization must compare it. A stale dispatcher must not finalize a newer claim.
9. **No terminal-state regression.** Compare-and-set transitions and TaskRuntime's authoritative version/state must prevent late dispatch or reconciliation from overwriting succeeded, failed, cancelled, or blocked states.
10. **Fail closed on schema mismatch.** Startup readiness must validate the exact migration contract before serving requests.

## Integration blockers identified

### Canonical digest mismatch

The coordinator currently hashes a request envelope containing tenant, owner, and canonical subtask fields including the selected agent's action. The PostgreSQL repository currently recomputes a digest over a different normalized subtask representation (subtask ID, agent ID, description, capabilities, permissions, dependencies, approval flag), without the same envelope or action field.

These are not interchangeable digests. Before wiring the port, define one shared, versioned canonicalization function used by both coordinator and repository. Reject unknown digest versions and mismatches; do not weaken validation to make existing values pass.

### Outbox attempt fencing gap

The outbox currently marks a row `processing` and increments `attempts`, but does not issue a unique claim-attempt token. Recovery can return a stale processing row to `pending`; a later worker can reclaim it while the reservation token and reservation generation remain unchanged. The former worker may then still satisfy the current `mark_enqueued` reservation checks.

Before production dispatch, add a per-claim token (or claim generation) and require it in the finalization update alongside tenant, owner, plan, subtask, reservation token, and reservation generation. Recovery/reclaim must invalidate the previous claim. Test the race with two repository instances and a deliberately delayed former claimant.

### Lifecycle coverage gap

The PostgreSQL repository currently exposes create/read, reservation, outbox claim/finalization, stale-outbox recovery, and close. It does not yet expose the complete conditional state-transition and cancellation/reconciliation operations needed by the coordinator. Do not add a constructor switch until those operations and coordinator-level integration tests exist.

## Required implementation sequence

1. Introduce a typed repository port and a SQLite adapter that preserves current behavior and existing test compatibility.
2. Move coordinator SQL behind that port without changing authorization or lifecycle semantics; keep SQLite as the only active backend in this step.
3. Implement PostgreSQL operations for the entire port, including state transitions, cancellation, reconciliation, and per-claim fencing.
4. Add coordinator-level PostgreSQL integration tests for submit/idempotency, ownership isolation, DAG scheduling, approval waiting, cancellation races, task-owner mismatch, concurrent advances, crash recovery, and stale-claim fencing across two instances.
5. Add explicit backend selection. PostgreSQL selection must fail startup if configuration or schema readiness is incomplete; it must never silently fall back to SQLite.
6. Document and test an explicit migration/rollout strategy for existing SQLite plans. Do not infer tenant/owner for historical rows; unowned legacy plans require a deliberate migration or remain unavailable in the new backend.
7. Only then consider production cutover. Keep merge, deployment, and data migration as separately authorized operations.

## Validation status

This is an architecture contract derived from the current coordinator and repository source. No code path was switched, no database migration was executed, and no tests were run as part of this document. The PostgreSQL integration suite still requires `YUKI_TEST_POSTGRES_URL`.
