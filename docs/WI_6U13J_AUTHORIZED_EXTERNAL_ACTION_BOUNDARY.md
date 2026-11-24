# WI-6U13J — Authorized external-action execution boundary

## Decision

Do not connect the WI-6U13I journal directly to `SpecialistTaskWorker` or expose a generic provider-call tool yet. The journal is a persistence primitive, not an authorization boundary. A safe action executor requires a task/tenant ownership model and one transaction that binds the verified worker delegation to the operation intent.

This is a design gate, not an implementation of provider execution. Existing specialist handlers remain text-generation-only. No external mutating action is enabled.

## Findings from the current implementation

1. `workflow_runs` (migration 0007) has no `tenant_id`, `owner_principal_id`, or `agent_id`. The operation journal's `tenant_id` is therefore currently caller-supplied metadata, not a tenant relationship enforced by the task schema.
2. `PostgresExternalOperationStore.claim_next()` currently claims from the global pending set and accepts only a worker ID. It does not constrain tenant, agent, task, or delegated scope.
3. `TaskRuntime._verify_worker_credential()` already verifies the signed workload credential, task ID, lease generation, scope, central authorization decision, and delegation preflight. When delegation is present it returns a transaction fence for the task store to execute with its protected mutation. The helper currently returns only lease generation and fence, not the verified claims needed to bind an external operation's tenant/agent/subject.
4. `PostgresDelegationAuthority.verify_in_transaction()` can revalidate a delegation on the caller's PostgreSQL connection and write its verification event in the same transaction. This is the right race-resistant primitive, provided the operation journal, task row, and delegation authority share the same database.
5. The external-operation store currently validates a supplied reconciliation callback, but its journal is not yet composed into `ProductionSecurityRuntime` or a task runtime. No production bootstrap currently owns this store.

## Required architecture

### 1. Establish durable task ownership

Add a workflow ownership migration before action execution:
- Add non-null tenant and initiating-principal ownership for newly created tasks.
- Add an agent/workflow ownership field only if it represents a real, trusted assignment; do not infer it from model output or a task name.
- Existing rows must be backfilled from an authoritative deployment-owned mapping. If no mapping exists, mark them explicitly as legacy/unassigned and prohibit external actions for them. Never assign a default tenant.
- Enforce tenant/task consistency with a composite unique key and foreign key from `external_operations` (or an equivalent database-enforced relationship).
- Make task creation accept trusted ownership context from the authenticated application layer; never accept tenant ownership from model-generated task definitions.

### 2. Expose a narrow verified workload context

Refactor the task credential verification path without weakening existing task mutation behavior:
- Return a typed, immutable verified context containing subject, tenant, agent, task, lease generation, allowed scopes, delegation ID, and transaction fence.
- Construct this context only after signature/trust/time/replay checks, task/lease binding, central gateway allow plus audit, and delegation preflight.
- Require the exact action scope (for example, `external-action:calendar.create`) rather than a broad `external:execute` permission.
- Keep human initiator and workload executor as distinct principals. Require task approval when the task definition or action risk policy requires it.
- Do not log or persist the credential itself.

### 3. Atomically create the intent

The action boundary must:
1. Load the task and verify tenant ownership against the authenticated human/task owner.
2. Verify the worker credential against the current task lease generation.
3. Ask the central `AuthorizationGateway` to authorize the exact action, resource, task, approval, and policy revision; audit failure denies.
4. In one PostgreSQL transaction, lock the workflow row, verify it is still running under the same owner/generation and unexpired database-clock lease, call `verify_in_transaction()` on the same connection, and insert the operation plus `operation.created` event.
5. Enforce tenant/task/action/idempotency uniqueness in the database. A reused key with a different canonical request digest is a conflict.
6. Persist only bounded normalized metadata and digests, never raw secrets, tokens, or provider request bodies.

The operation journal should accept a transaction callback only from the trusted service boundary, not an arbitrary model/tool caller. Verify shared database identity across workflow repository, delegation authority, and operation store at composition time.

### 4. Scope worker claims

Replace the global claim method for production use with a claim API that requires trusted tenant, agent, task, action scope, worker identity, and current lease generation. The claim SQL must constrain the candidate rows to that exact authorized context. A worker must not be able to claim another tenant's operation by knowing or guessing an operation ID.

Completion must continue to require operation lease owner, generation, version, and unexpired database-clock lease. Also recheck task lease and delegation before dispatch/commit boundaries. Do not hold a database transaction open across a network request.

### 5. Provider dispatch semantics

Only implement a provider adapter after selecting a concrete provider/API and its idempotency and lookup semantics:
- Send the same provider idempotency key for every retry of the same logical operation.
- Persist provider request identity before dispatch; persist provider reference and normalized result digest after success.
- A timeout or worker crash after dispatch is `unknown`, not a retryable failure.
- If the provider supports lookup by idempotency key/reference, reconciliation may resolve the outcome.
- If it supports neither idempotency nor lookup, do not automatically retry ambiguous operations; require an operator decision and preserve the unknown state.
- No claim of exactly-once side effects. The achievable guarantee is durable intent plus at-most-once dispatch by Yuki unless provider-side idempotency/reconciliation supplies stronger semantics.

## State and transaction rules

- Operation creation + created event + task/delegation verification event: one transaction.
- Claim + claimed event: one transaction.
- Completion/failure/unknown + event: one transaction.
- Expired dispatch lease: transition to `unknown`; never reset to `pending`.
- Reconciliation is separately authorized, actor-attributed, version-checked, and cannot requeue an operation.
- Any policy, audit, credential, database, or delegation uncertainty fails closed.

## Acceptance tests required before enabling any action

1. Tenant A cannot create, read, claim, reconcile, or complete tenant B's operation.
2. Tenant/task composite relationship rejects mismatched ownership even if a caller supplies a valid-looking tenant ID.
3. Missing, malformed, expired, replayed, wrong-audience, wrong-task, wrong-generation, or wrong-scope workload credentials are denied.
4. Revoked, expired, wrong-tenant, wrong-agent, wrong-task, wrong-generation, or insufficient-scope delegation is denied.
5. Delegation revocation racing with intent creation serializes correctly; no operation intent commits after a revocation that wins the transaction order.
6. Task lease expiry/reclaim racing with intent creation cannot create an operation under a stale generation.
7. Gateway deny, stale policy, audit failure, or verifier outage prevents intent creation.
8. Concurrent duplicate requests with the same idempotency key and digest return one operation; different digest conflicts.
9. Claims are tenant/agent/task/action scoped; concurrent workers cannot claim the same operation.
10. A stale worker cannot dispatch/complete after lease expiry or generation change.
11. Crash before dispatch leaves a safely inspectable intent; crash after provider acceptance becomes unknown and is not blindly replayed.
12. Provider idempotency and reconciliation tests use a deterministic fake provider with explicit simulated acceptance/timeout/crash points.
13. Raw credentials, tokens, and provider payloads never appear in DB rows, operation events, logs, or test snapshots.
14. Production readiness fails if workflow, delegation, authorization audit, or operation journal database identities differ.

## Delivery sequence

- WI-6U13J-A: tenant/task ownership schema and trusted task-creation propagation, with explicit legacy-row handling.
- WI-6U13J-B: typed verified workload context from `TaskRuntime`; preserve existing task mutation behavior.
- WI-6U13J-C: same-transaction authorized intent creation and scoped operation claims.
- WI-6U13J-D: fake-provider crash/reconciliation harness and acceptance tests.
- WI-6U13J-E: select one provider and implement a disabled-by-default adapter only after provider semantics and user authorization are explicit.

## Release gates

No production external action is enabled until all acceptance tests pass on exact-head PostgreSQL CI, migrations are reviewed and rehearsed against a copy of production data, task ownership backfill is proven authoritative, and an operator runbook covers unknown outcomes and provider reconciliation. This design does not authorize merging or deployment.
