# WI-6U13J-C — Same-transaction authorized operation intents and scoped lifecycle

## Implementation

Migration `0011_external_operation_authorization_context.sql` adds nullable legacy-compatible columns for `agent_id`, `task_lease_generation`, `authorization_decision_id`, and `policy_revision`. A check constraint requires these fields to be either all absent (legacy journal rows) or all present (authorized rows). A partial index supports scoped pending claims.

`AuthorizedExternalActionService` composes `TaskRuntime`, `PostgresDelegationAuthority`, and `PostgresExternalOperationStore`. Construction fails unless all three repositories report the same PostgreSQL database identity, central authorization and workload verification are configured, and workload delegation is mandatory.

The service verifies the credential and central policy decision through `TaskRuntime.verify_workload_context()`. The store then locks the durable workflow row and rechecks tenant, initiating principal, ownership status, worker subject, task state, task lease generation, task lease expiry, and credential expiry using the database clock. It executes the delegation transaction fence on that same connection before writing the operation row and `operation.created` event in one transaction.

The idempotency digest binds tenant, task, agent, task lease generation, step, exact action, action version, input digest, and policy revision. The journal stores only digests and bounded identity/audit metadata; it does not store raw credentials, signing keys, or provider payloads.

Operation claims are scoped by tenant, agent, task, exact action, task lease generation, and policy revision. Expired in-flight operations become `unknown` and are not replayed. Pending operations tied to a superseded task lease or policy revision become terminal with an audit event. Dispatch authorization and completion re-lock the task, revalidate delegation in-transaction, and fence on the operation owner, generation, version, and expiry.

Unscoped store entrypoints for creation, claiming, completion, failure, lookup, and event listing are now internal-only helpers. The service exposes only context-bound APIs. Operator reconciliation remains a separate explicit authorization callback path.

## Safety boundaries

- No provider dispatcher or network client is added.
- `authorize_dispatch()` is a just-in-time database gate; callers must not hold a database transaction open across a provider request.
- A crash or timeout after provider acceptance remains ambiguous and must transition to `unknown`; this work does not claim exactly-once external effects.
- Existing legacy operation rows remain unbound and cannot be claimed through the authorized path.
- Production readiness now checks that migration 0011 columns exist.

## Tests

PostgreSQL integration coverage exercises authorized idempotent intent creation, persisted identity metadata, action-scoped claims, authorized reads, just-in-time dispatch authorization, completion fencing, delegation revocation between preflight and intent creation, task lease expiry between preflight and intent creation, stale task lease completion rejection, and split-database composition rejection. The suite is wired into PostgreSQL CI and has not been run locally in this connector-only environment.
