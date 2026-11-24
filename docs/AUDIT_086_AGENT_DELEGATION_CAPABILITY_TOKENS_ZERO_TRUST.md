# Audit 086 — Agent Delegation, Capability Tokens, Identity Propagation, and Zero-Trust Execution

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Scope:** specialist-agent coordinator, task runtime, workflow approval/store, specialist worker, capability catalog/manifest, and permission contracts.

## Executive summary

Yuki has useful foundations for a zero-trust agent system: explicit authenticated principals for workflow approval, session-bound approval digests, exact-true authorization callbacks, allow-listed specialist actions, deterministic capability/permission routing, bounded tasks, worker leases, and audited task state transitions.

However, the current design is primarily manifest-based and caller-trust-based. It does not yet issue a cryptographically verifiable, attenuated delegation grant that follows a specialist task through worker claim and execution. A specialist's declared permissions are routing metadata, not a cryptographic execution credential. The worker's service actor and prebuilt handler map are trusted composition inputs.

## Findings

### P1 — No signed, attenuated agent delegation credential

The specialist coordinator checks required capabilities and permissions against a registered `SpecialistAgent` manifest during planning. The queued workflow definition contains a specialist task name, action, argument, and approval flag. The task record does not carry a signed grant binding the initiating principal, tenant, agent identity, exact task, allowed action, resource scope, permission set, policy revision, expiry, and revocation reference.

Consequently, the worker cannot independently verify that a task is still operating within the authority originally delegated to that specialist.

**Recommendation:** issue short-lived signed grants with strict audience and task binding. Claims should include issuer, subject/principal, agent ID, tenant/workspace, task ID, allowed actions, resource constraints, permission attenuation, policy/registry revision, issued-at, expiry, unique token ID, and revocation/version reference. Workers must validate signature, issuer, audience, expiry, task/action/resource binding, current policy revision, and revocation immediately before execution and before each side effect.

### P1 — Specialist permissions are not execution-time authorization

The coordinator uses `required_permissions` to choose an agent whose manifest contains those permissions. The specialist worker later maps the task name to a handler and invokes that handler; it does not resolve the agent manifest or independently compare the task's required permissions with the current registry/policy.

This means the manifest check occurs at planning/dispatch, not at the final execution boundary. Registry changes after dispatch are not inherently enforced by the worker.

**Recommendation:** revalidate the agent enabled state and effective permission grant at claim and immediately before execution. Prefer a grant snapshot plus a current revocation/policy-version check. If the agent is disabled or permissions reduced after enqueue, fail closed or cancel the task with an explicit policy-revoked outcome.

### P1 — Worker service identity is a broad trust boundary

`SpecialistTaskWorker` receives a `service_actor_id` and a mapping of callable handlers. Task completion/failure uses this service actor, while task execution is authorized by the fact that a worker claimed the queued record. The worker does not receive an authenticated workload identity or per-task delegated credential from the task store.

**Recommendation:** use workload identity for each worker pool, bind worker identity to deployment and queue, and exchange it for a narrowly scoped task execution grant. The service actor should not be a universal bypass identity. Separate permissions for claim, execute, artifact write, and completion.

### P2 — Agent registry is global and not tenant/workspace scoped

The specialist registry is keyed by global agent ID and stores capabilities, permissions, action, and enabled status. There is no tenant/workspace column in the inspected registry schema.

**Recommendation:** scope agent identities and manifests to a tenant/workspace, while optionally maintaining a separately governed platform-level catalog of immutable agent templates. Tenant bindings, provider preferences, secrets, memory, and artifacts must be isolated.

### P2 — Permission strings do not encode resource scope or constraints

Manifest permissions are simple strings such as `web:read` or `artifact:write`. They do not express resource identifiers, allowed domains, record ownership, data classifications, operation limits, or constraints such as read-only versus write.

**Recommendation:** model capabilities as structured grants: action, resource type/ID, tenant, data classification, constraints, conditions, and expiry. Apply deny-overrides and least privilege. Avoid interpreting a broad string permission as universal access.

### P2 — Revocation and policy-version propagation are not defined end-to-end

The agent registry supports enable/disable and refreshes from SQLite before planning/advance, but queued tasks and worker handler maps are not bound to a policy revision or revocation epoch. There is no demonstrated token revocation list or worker-side policy invalidation channel.

**Recommendation:** version each agent manifest and tenant policy. Bind grants to those versions, maintain a durable revocation/disable epoch, and require workers to check it before execution. Define bounded cache staleness and fail closed when the control plane is unavailable for high-risk tasks.

### P2 — Approval and delegation are separate concepts and must remain separate

Workflow approval binds an approval to a trusted principal/session and exact request digest. This is a strong human authorization control, but it does not itself prove which specialist is executing, what authority was delegated, or whether that authority remains valid.

**Recommendation:** require both: a human approval where policy demands it, and an independently verifiable agent delegation grant. Bind approval digest to the task definition and delegation grant ID/revision so neither can be swapped after approval.

## Existing positive controls

- Workflow approval uses a trusted principal resolver and rejects untrusted resolver results.
- Approval requires explicit workflow permission and resource authorization.
- Approval binds actor, session hash, request digest, and task version.
- Task claim is atomic and approval-aware.
- Worker completion/failure is fenced by lease owner and version.
- Specialist manifests validate bounded IDs, capability names, permission names, and allowed action.
- Agent routing checks required capabilities and permissions and selects the least-overprovisioned enabled specialist.
- Specialist worker uses an explicit handler map and only accepts the bounded `emit` action.
- Provider specialist handlers treat supplied content as untrusted, request text-only output, and bound input/output.
- Cloud provider routing requires explicit opt-in and does not silently fall back.
- Task events and state transitions are persisted with version checks.

## Recommended zero-trust delegation protocol

1. **Authenticate principal:** derive user/service identity from trusted session or workload identity, never task payload.
2. **Resolve tenant:** load membership and tenant policy from authoritative control plane.
3. **Plan:** produce bounded task DAG; required permissions are constraints, not authority.
4. **Authorize delegation:** intersect principal authority, tenant policy, agent manifest, and task resource scope.
5. **Mint grant:** sign a short-lived, task-specific, privilege-attenuating credential.
6. **Approve:** where required, bind human approval to exact task digest and grant revision.
7. **Claim:** worker authenticates with workload identity and obtains only tasks assigned to its pool.
8. **Verify:** validate signature, issuer, audience, task, action, resource, tenant, expiry, revocation, and policy revision.
9. **Execute:** recheck grant before each side effect; use separate capability checks for tools, data, memory, and artifacts.
10. **Audit:** record grant ID/hash, issuer, subject, agent, task, policy revision, decision, and side-effect outcome without storing bearer grants or secrets.

## Required regression/adversarial tests

- A grant for one task cannot be replayed for another task, agent, tenant, action, or resource.
- Expired, malformed, wrong-audience, wrong-issuer, and invalid-signature grants are rejected.
- Agent disable or permission reduction after enqueue blocks worker execution.
- A worker service identity cannot complete or execute tasks outside its queue/pool scope.
- Revoked grant IDs and stale policy revisions fail closed.
- A specialist cannot receive permissions broader than the initiating principal.
- Human approval cannot be reused after task definition or delegation grant changes.
- Cross-tenant agent IDs, memory, artifacts, and provider credentials remain isolated.
- Control-plane unavailability follows explicit fail-closed policy for high-risk operations.
- Every side-effecting action revalidates grant and resource authorization immediately before execution.

## Validation limits

Source inspection only. No runtime code changed and no tests were executed. The audit reviewed workflow approval, task claim/lease fencing, specialist routing and worker dispatch. No cryptographic grant format, key rotation, workload identity integration, multi-tenant deployment, revocation propagation, or side-effect execution test was performed. The existing specialist worker is bounded to text artifact generation; findings about broad host-side effects are future architectural requirements, not claims that this worker currently executes arbitrary tools.
