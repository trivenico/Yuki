# WI-6U14-B — Tenant-scoped agent plan ownership

## Implemented boundary

The coordinator now derives plan ownership from its central trusted principal resolver whenever the workflow repository declares tenant ownership support. It also requires the TaskRuntime central gateway and resolver to be configured and verifies that both resolvers return the exact same Principal. The principal must be human, its principal ID must match the authenticated actor, and its tenant ID must be non-empty.

Tenant and owner values are never accepted as public submit arguments. They are persisted on the plan row and returned as immutable plan metadata.

## Idempotency

For tenant-owned plans, the existing globally unique idempotency column stores a deterministic SHA-256 scope key derived from (tenant_id, owner_principal_id, client_idempotency_key). The original client key is stored separately for display. The request digest includes tenant and owner context. Thus:

- same tenant + same owner + same key + same request returns the same plan;
- same scope + same key + different request conflicts;
- different owners or tenants can use the same client key without colliding.

The deterministic plan ID is derived from the scoped key. The raw client key is not used as a global database uniqueness key.

## Persistence enforcement

Plan read, dispatch, and cancellation first load the plan with tenant and owner predicates. Subtask status/task binding updates use a parent-plan ownership predicate. Cross-tenant or cross-owner lookups return the same not-found behavior as nonexistent plans.

Legacy plans are migrated with null ownership. They remain accessible only through the explicitly legacy, non-tenant-owned runtime mode. A tenant-owned runtime cannot read, advance, or cancel them; there is no implicit reassignment to the current principal.

## Compatibility and limitations

This change retains the existing SQLite coordinator repository. It adds ownership enforcement and scoped idempotency, but it does not turn SQLite into a shared multi-instance production repository, nor does it implement the PostgreSQL plan repository or atomic cross-database dispatch reservation proposed in WI-6U14-A.

The specialist registry remains deployment-global in this increment. Tenant-specific agent registration is not introduced.

Task dispatch continues through TaskRuntime using the same actor, so the runtime independently derives the task's tenant and owner. A future PostgreSQL coordinator repository must additionally enforce parent-plan/task ownership with composite foreign keys or equivalent transactional checks and provide crash-safe reservation/reconciliation.

## Validation status

Regression tests cover tenant-scoped idempotency for same and different owners, same client key across tenants, and denial of cross-owner/cross-tenant plan reads. Tests are added but have not been run in this connector-only workflow. Merge only after exact-head CI passes.