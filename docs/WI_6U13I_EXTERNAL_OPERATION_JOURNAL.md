# WI-6U13I — PostgreSQL external-operation journal

## Scope

This change adds a durable, tenant-scoped journal for a future explicitly authorized external action adapter. It does not add an action dispatcher, provider client, network call, generated-code execution, or a claim of exactly-once execution. Existing specialist handlers remain text-only.

## Safety model

- An operation intent is created before any future provider dispatch.
- A caller supplies a stable idempotency key and a SHA-256 digest of canonical validated input. The store derives a request digest from tenant, task, step, action name/version, and input digest. Reusing a key for different intent is a conflict.
- Raw inputs, provider credentials, and provider payloads are not persisted. The journal stores digests and bounded metadata only.
- Claims use PostgreSQL `FOR UPDATE SKIP LOCKED`, DB-clock leases, monotonically increasing lease generations, and optimistic versions.
- Completion/failure requires the current owner, generation, version, and unexpired DB lease.
- An expired in-flight operation becomes `unknown` and is not claimed again. This deliberately avoids blind replay after a crash that may have occurred after provider acceptance.
- Reconciliation is a separate caller-authorized operation, records its actor and evidence digests, and cannot move an operation back to `pending`.
- The store does not validate authorization or delegation itself. The future dispatcher must call the central authorization gateway and verify the task/tenant/agent/lease-generation-bound delegation before creating or dispatching an operation. The journal is not a substitute for those controls.

## State model

`pending -> inflight -> succeeded | failed_terminal | unknown`

`unknown | reconciliation_required -> succeeded | failed_terminal | reconciliation_required`

No automatic transition returns an ambiguous operation to `pending`. Provider idempotency and provider-side lookup/reconciliation must be designed for a specific provider before a mutating adapter can be enabled.

## Validation

PostgreSQL integration tests cover idempotent creation, key/input conflicts, fenced claim and completion, atomic event history, expired-lease unknown handling, stale worker rejection, explicit reconciliation, and terminal non-replay. The GitHub Actions job compiles the implementation and runs these tests against PostgreSQL 16. Tests are not run locally in this connector-only workflow.
