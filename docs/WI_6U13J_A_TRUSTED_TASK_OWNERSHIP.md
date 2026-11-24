# WI-6U13J-A — Trusted workflow task ownership

## Implemented boundary

Migration `0010_workflow_task_ownership.sql` adds `tenant_id`, `owner_principal_id`, and `ownership_status` to PostgreSQL workflow records.

- Existing tasks are explicitly marked `legacy_unassigned`. No tenant or owner is guessed, backfilled from task text, or assigned a shared default.
- Newly created tasks can be marked `owned` only when both tenant and authenticated principal are supplied together.
- PostgreSQL task creation includes tenant and owner in the idempotency request digest, so the same key cannot silently bind a task to a different owner.
- `TaskRuntime.enqueue` detects the production PostgreSQL ownership capability and requires central authorization plus a `Principal` returned by the trusted `principal_for_actor` resolver with a non-empty tenant ID. The tenant is taken from that authenticated principal, never from task definitions or model output.
- PostgreSQL returns the ownership metadata on `WorkflowRun`; SQLite/local tasks remain compatible and have the explicit dataclass default `legacy_unassigned`.
- A composite foreign key ties `external_operations(tenant_id, task_id)` to the parent workflow task's `(tenant_id, run_id)`. It is added `NOT VALID` so pre-existing rows do not block the migration; PostgreSQL enforces it for new operation inserts. An operation cannot be created for a legacy/unassigned task or under another tenant.

## Compatibility and rollout

No historical ownership is inferred. Legacy tasks remain readable under existing authorization paths, but they cannot be the parent of a new external operation. Production task creation on the PostgreSQL store fails closed if central authorization or authenticated tenant context is absent.

The migration deliberately does not claim that tenant ownership alone authorizes task reads, worker claims, or actions. Those operations still require their existing authorization and lease/delegation checks. The next WI-6U13J stages will bind verified workload claims to operation creation and scope operation claims by tenant/task/agent/action.

## Tests

PostgreSQL 16 coverage applies the ordered workflow, operation, and ownership migrations; verifies owner metadata persistence; verifies production task creation fails closed without a trusted tenant principal; and exercises the operation/task composite foreign-key boundary. Tests are wired to CI but have not been run locally in this connector-only environment.
