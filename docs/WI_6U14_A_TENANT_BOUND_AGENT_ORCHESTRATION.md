# WI-6U14-A — Tenant-bound agent orchestration boundary

## Audit finding

`automation/agent_coordinator.py` persists `agent_plans` and `agent_plan_subtasks` in a separate SQLite database. The plan row stores a globally unique idempotency key and request digest, but no tenant ID or initiating principal. `plan_id` is deterministically derived from the idempotency key alone. Plan read, advance, and cancellation call an authorization callback with actor, action, and plan ID, but the plan record itself cannot independently prove tenant ownership or bind the plan to its initiating principal.

This creates a tenant-isolation and idempotency-domain gap for deployments where multiple tenants share one coordinator database. A tenant can choose the same idempotency key as another tenant and collide with that plan's globally unique key. Whether that collision is disclosed or acted upon depends on the injected authorization callback; the persistence layer has no tenant predicate or immutable owner binding to enforce.

The task runtime's PostgreSQL ownership path separately derives tenant and owner principal from `principal_for_actor` when creating a task. That protects the resulting workflow task, but it does not retroactively tenant-bind the coordinator plan, its idempotency namespace, or its subtask rows. The coordinator and task store therefore have different ownership authorities.

## Scope

This is a design and audit record, not a claim that an exploit has been demonstrated. The impact depends on deployment topology and the authorization callback. It is a correctness/security gap that must be closed before treating the coordinator as a multi-tenant production control plane.

## Required invariants

1. Every plan has immutable `tenant_id` and `owner_principal_id` obtained from trusted authenticated principal context, never from model output or caller-supplied plan fields.
2. Idempotency uniqueness is scoped to tenant and owner (or an explicitly documented tenant-wide scope): `(tenant_id, owner_principal_id, idempotency_key)`.
3. Plan identifiers are tenant-scoped or cryptographically/randomly generated; they must not disclose cross-tenant idempotency collisions.
4. Every read, advance, cancel, status refresh, and subtask mutation includes tenant and owner predicates in the persistence operation, in addition to central authorization.
5. A dispatched task's tenant and owner must exactly match its parent plan's immutable tenant and owner. Mismatch fails closed and emits a bounded security event.
6. Registry configuration is explicitly classified as deployment-global or tenant-scoped. If tenant-scoped agents are supported, registry rows and routing queries must include tenant; otherwise tenant-specific registration is rejected.
7. Plan creation and subtask persistence are durable and concurrency-safe across processes. A crash between creating a task and recording its task ID must recover by tenant-scoped idempotency without creating duplicate tasks.
8. Dependency outputs remain untrusted data. The orchestration layer must not convert them into authority, permissions, action definitions, or trusted instructions.

## Recommended implementation

### A. Trusted ownership context

Introduce an immutable coordinator ownership context sourced from the same trusted `AuthorizationGateway` and principal resolver used by `TaskRuntime`. Require a human initiating principal with a non-empty tenant. Do not accept tenant or principal IDs as independent public arguments. Keep any legacy single-tenant mode explicitly isolated and disabled for tenant-owned production repositories.

### B. Durable tenant-scoped plan repository

Move plan and subtask persistence behind a repository protocol. The production PostgreSQL implementation should share the workflow database identity and use tenant/owner composite foreign keys where available. Add a migration that backfills legacy plans as unassigned; unassigned plans must not be advanced or used to dispatch tasks until an operator performs an explicit, audited ownership reconciliation. Do not silently assign old plans to the current caller.

Use composite uniqueness for idempotency scope and tenant-qualified lookups for all mutations. Keep SQLite only as an explicitly single-user development/test adapter if required; do not present it as a multi-tenant or multi-instance production store.

### C. Atomic dispatch/recovery protocol

Do not hold coordinator database locks while calling the workflow runtime. Persist a dispatch reservation with a stable tenant-scoped idempotency key, then enqueue using the same trusted owner context, then finalize the subtask-to-task binding using compare-and-swap. On restart, reconcile reservations against the workflow repository. Recheck plan ownership, agent enabled state, permissions, and dependency completion immediately before enqueue.

### D. Authorization and registry

Central authorization must decide the exact plan action against tenant, owner, plan, and agent resource. Persistence must independently enforce the same tenant/owner scope. Document whether specialist definitions are deployment-global or tenant-owned; do not mix the two models.

## Acceptance tests

- Two tenants may submit the same idempotency key and receive distinct plans; same tenant/owner/key with same digest is idempotent; changed digest conflicts only within that scope.
- Tenant B cannot read, advance, cancel, inspect subtasks, or infer existence of tenant A's plan, even if an authorization callback is misconfigured to allow a generic action.
- Plan owner and tenant are immutable; legacy unassigned plans cannot dispatch.
- A plan cannot bind a workflow task with a different tenant or owner principal.
- Crash at each boundary (reservation, task enqueue, task binding) recovers exactly one workflow task and one plan binding.
- Concurrent advances from multiple coordinator instances cannot duplicate dispatch or bypass dependency gating.
- Registry tenant/global semantics are tested explicitly, including cross-tenant visibility and mutation.
- Dependency output remains untrusted and cannot expand capabilities or alter the registered action/permission manifest.

## Release gate

Do not claim multi-tenant agent orchestration readiness until the production repository, trusted owner context, scoped idempotency, task-parent binding, recovery protocol, and cross-tenant integration tests are implemented and exact-head CI is green.