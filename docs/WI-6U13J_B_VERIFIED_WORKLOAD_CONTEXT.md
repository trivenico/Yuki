# WI-6U13J-B — Verified workload context for authorized actions

## Purpose

Expose the verified workload identity to downstream action orchestration without passing around a raw credential or reducing the identity to a lease-generation string.

## Implementation

`automation.task_runtime.VerifiedWorkloadContext` is an immutable value created only after `TaskRuntime` has:

- Verified the signed workload credential, audience, subject, task ID, lease generation, and requested action scope.
- Matched the credential tenant to the durable task tenant and rejected PostgreSQL tasks still marked `legacy_unassigned`.
- Checked any delegation grant, including grantee, tenant, agent, task, lease generation, and action scope. The returned context retains the transaction-bound delegation fence for the next stage's atomic operation creation.
- Obtained an audited allow from the central authorization gateway for the workload principal.

The context includes issuer, subject, tenant, trusted initiating principal, agent, audience, token ID, task ID, lease generation, credential scopes, delegation ID, credential expiry, the single action authorized by this verification, the authorization decision ID and policy revision, and the delegation transaction fence. It never includes the credential or signing key.

`TaskRuntime.verify_workload_context()` is the downstream boundary. It requires the PostgreSQL ownership-aware repository, a task with immutable owned metadata, an active worker lease, and central authorization. It fails closed for legacy/unassigned tasks, missing gateway, invalid credentials, tenant mismatch, missing action scope, or denied authorization.

Existing task mutation methods retain their prior `(lease_generation, delegation_fence)` compatibility interface through a private adapter. This keeps the change additive while allowing the external-operation path to consume the richer context.

## Security constraints and limitations

- The context is evidence of one verified action decision, not a reusable bearer token or blanket permission for every credential scope.
- Consumers must check `authorized_action` against the exact operation and must execute `delegation_fence` inside the same PostgreSQL transaction that creates the durable operation intent.
- A credential's raw token and secret signing material are never persisted or returned.
- This stage does not create operation intents, claim operations, dispatch providers, or enable external side effects. Those are WI-6U13J-C and later.
- The delegation transaction fence revalidates the grant chain using the PostgreSQL authority's database clock in the caller's transaction.

## Tests

PostgreSQL integration coverage verifies context creation from a verified-claims test double, exact tenant/task/lease binding, initiating principal, audited decision metadata, immutable context, action-scope rejection, and cross-tenant rejection. Tests are wired into the existing PostgreSQL workflow suite; they have not been run locally in the connector-only environment.
