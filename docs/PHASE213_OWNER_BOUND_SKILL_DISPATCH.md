# Phase 213 — Owner-bound durable skill dispatch

## Objective

Carry authenticated owner provenance from durable goal storage through the current fenced worker claim and into the guarded skill runtime. A valid lease token proves which worker holds a claim; it does not, by itself, prove whose task is being executed.

## Implementation

- Added `DurableTaskEngine.get_claim_identity(task_id, worker_id, lease_token)`. It validates the exact live claim and reads the goal owner in one SQLite read transaction.
- Legacy goals remain unowned. The worker rejects tasks attached to those goals rather than fabricating an owner or accepting identity from task payloads.
- `DurableSkillWorker` constructs immutable `SkillPolicyContext` only from the engine-derived owner, goal and task identifiers, and passes it to the runtime.
- `ApprovedSkillRuntime` and `invoke_registered_skill` accept and validate the trusted context.
- One-shot invocation approvals are now scoped by principal, skill name and exact argument digest. An approval created for one owner cannot authorize another owner's task.
- Added tests for owner derivation, unowned legacy tasks, cross-owner approval rejection and normal owner-bound execution.

## Trust boundary

The owner ID is read from the durable goal row after validating the live lease. It is never accepted from task payloads, model output, worker-provided metadata or runtime arguments. The context value validates shape; it is not itself an authentication mechanism.

Any application-facing approval operation must derive `principal_id` from the authenticated owner session. The optional `principal_id` parameter on the local runtime approval helper is a trusted-service API, not an HTTP authorization boundary.

## Limitations — not production complete

- One-shot approvals remain process-local and are not durable across restart.
- This phase does not yet integrate the encrypted approval credential broker or central capability approval gateway.
- Approval is not yet bound to lease generation, worker identity, capability request digest and expiry in a durable one-shot record.
- No authenticated durable-task HTTP routes are added here.
- Local SQLite fencing cannot stop an already-running external provider operation. Provider adapters still require their own idempotency/commit fencing and reconciliation.
- Worker lease expiry cannot prove a previous process has stopped. Recovery must revoke credentials and require fresh approval before reassignment.
- The runtime is not a sandbox; skill handlers remain trusted code and require process/container isolation appropriate to their risk.

## Acceptance evidence

Regression coverage is added for the owner-bound worker path and cross-owner approval isolation. Exact-head CI must pass before this phase can be considered validated. No local test execution, external provider certification, or production release is claimed.
