# Specialist agent plan ownership

Specialist plans are private to the actor that created them. Authorization callbacks remain necessary, but authorization alone is not a substitute for an explicit persisted owner check.

## Current storage contract

- Every newly created plan stores its `owner_id`.
- The caller's idempotency key is stored separately from an opaque SHA-256 owner-scoped idempotency key. The same client key may therefore be reused by different actors without collisions or cross-actor plan disclosure.
- Plan IDs are derived from the owner and caller idempotency key.
- `get_plan`, `advance`, and `cancel_plan` verify persisted ownership after the normal action authorization check.
- A repeated key from the same owner returns the existing plan only when the canonical plan digest matches. Reusing that owner's key for a different plan raises a conflict.
- Model-generated plans remain proposals. Capability routing, authorization, dependency validation and approval gates remain authoritative.

## Existing database migration

The coordinator adds `owner_id` and `request_key` columns to existing `agent_plans` tables. Rows created by older versions have no trustworthy persisted owner, so their migrated `owner_id` is empty. The coordinator deliberately fails closed when an actor attempts to read, dispatch or cancel such a legacy plan. It does not guess ownership from an idempotency key, plan contents, or a current authorization callback.

Before upgrading a database containing legacy plans, operators should stop plan workers, make a verified backup, inventory the legacy plan and task records, and perform an explicit administrative ownership reconciliation using an independently trusted audit source. Do not bulk-assign legacy plans to the first actor who requests them. Until reconciliation is implemented and reviewed, legacy ownerless plans remain inaccessible through the coordinator.

## Security tests

The test suite covers:
- two actors reusing the same caller idempotency key without sharing a plan;
- same-owner retries returning the same plan;
- cross-owner read, dispatch and cancellation denial;
- migration of the pre-owner schema without silently assigning legacy plans.

This is an application-level ownership control, not a claim of complete multi-tenant production isolation. Deployment identity propagation, audit logging, database access boundaries, task-runtime ownership and independent security review remain release gates.
