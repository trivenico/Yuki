# Audit 095 — Signed Agent Execution Grants, Capability Attenuation, and Revocation

Repository: trivenico/Yuki  
Base: main  
Scope: specialist plan creation and dispatch, task runtime authorization, approval binding, MCP tool policy, autonomy tiers, worker lease semantics.  
Outcome: architecture/security audit; no runtime patch submitted.

## Executive summary

Yuki has several useful authorization primitives, but they are not yet one end-to-end capability system. The specialist registry routes work using declared capabilities and permissions. TaskRuntime authorizes operations through an injected callback. ApprovalCoordinator and SQLitePendingApprovalStore bind a one-time approval to an actor and session. MCP policy has a stronger owner/provider/account/tool/risk/scope/version shape. Workflow leases fence concurrency using worker ID and expiry.

The missing link is a verifiable execution grant that carries authority from planning through dispatch to the actual side-effect boundary. In the inspected specialist workflow, required permissions are routing metadata; the task runtime does not receive a signed grant proving that a particular agent, worker, tenant, manifest revision, and task are allowed to perform a bounded set of actions on named resources. A worker lease is a concurrency token, not an authorization capability. An approval record is a one-time user consent artifact, not an agent identity or delegated authority token.

This audit recommends a single canonical grant contract integrated with the central AgentControlPlane roadmap, rather than separate per-agent tokens or scattered permission checks.

## Files inspected

- core/permissions.py
- core/approval_flow.py
- core/pending_approvals.py
- core/action_approvals.py
- core/tiered_autonomy.py
- automation/agent_coordinator.py
- automation/task_runtime.py
- automation/workflow_store.py
- automation/agent_task_journal.py
- core/auth.py
- core/mcp_runtime.py
- tests/test_pending_approvals.py
- tests/test_agent_coordinator.py
- tests/test_task_runtime.py

## Positive controls observed

1. PermissionGate fails closed when no approver exists, validates action/reason/risk, and accepts only literal True.
2. ApprovalCoordinator snapshots bounded JSON-safe action data, binds pending approval to actor and session, consumes once, and audits request/approval/denial/execution.
3. SQLitePendingApprovalStore persists actor/session hashes, enforces TTL/capacity, and atomically consumes approvals.
4. TieredAutonomyPolicy never silently overrides fresh approval for state-changing or external actions.
5. SpecialistAgentCoordinator validates capability/permission manifests and uses deterministic least-overprovisioned routing.
6. TaskRuntime uses per-action authorization callbacks; workflow leases use expiry, version fencing, and atomic claim.
7. MCP tool policy binds decisions to owner/provider/account/tool/risk/scopes/policy version and uses explicit owner confirmation for high-risk operations.
8. Tool execution and approval boundaries have separate controls; this separation is valuable and should be preserved.

## Findings

### P1 — Specialist permissions are routing metadata, not a delegated execution capability

SubtaskSpec carries required capabilities and required permissions. The coordinator selects an enabled specialist whose manifest satisfies those requirements and stores the selected agent ID/action with the plan. The task is later enqueued through TaskRuntime. The inspected path does not issue a signed, task-scoped credential that independently proves the selected agent may perform those actions.

Risk: a plan's routing decision and its declared permissions do not themselves constrain the worker at execution time. The worker's authority depends on separate TaskRuntime authorization callbacks and the downstream tool/skill boundary. This makes it difficult to prove that execution authority is no broader than the planner intended.

Required direction:
- Issue a grant only after an authenticated owner-authorized plan is approved.
- Bind the grant to tenant/owner, initiating principal, agent ID, immutable manifest digest, task ID, authenticated worker principal, allowed action set, resource constraints, policy revision, issue/expiry time, unique grant ID, and revocation epoch.
- Workers receive only the minimum grant needed for their assigned subtask.
- Enforce the grant at every protected side-effect boundary, not only at planning or task enqueue.
- Never expose signing keys or reusable grant credentials to prompts, model context, tool output, or logs.

### P1 — No common signed capability format, issuer, or verifier is established across execution paths

The reviewed workflow uses caller-supplied actor and worker labels plus injected authorization callbacks. MCP policy has its own scoped versioned policy records. ApprovalCoordinator binds user/session to a one-time action. These are separate mechanisms with different trust and lifecycle semantics.

Risk: each subsystem can make a locally reasonable decision without a common proof that the current action is authorized under the same principal, tenant, agent, task, policy revision, and revocation state. A capability added only to the coordinator would be cosmetic if MCP, skills, and other side-effect adapters do not verify it.

Required direction:
- Define one typed grant schema and canonical serialization.
- Use a trusted issuer and verifier with asymmetric signatures or a protected MAC service; keep signing material outside worker processes.
- Include issuer, subject, audience, tenant, task, agent, manifest digest, actions, resource constraints, not-before, expiry, grant ID, parent grant ID, policy revision, and revocation epoch.
- Reject unknown fields, malformed encodings, duplicate claims, invalid signatures, wrong audience, expired grants, and stale policy/revocation epochs.
- Verify at every tool, plugin, filesystem, network, and external-service boundary.

### P1 — Capability attenuation and delegation lineage are not represented in the task contract

No inspected task or plan interface persists a parent grant or a reduced child grant. Agent permissions are declared on registry manifests, but there is no cryptographically verifiable chain proving that a delegated specialist received a strict subset of its parent's actions/resources/time.

Risk: future multi-agent delegation could accidentally grant a child agent broader permissions than the parent or original owner approval. Permission strings alone cannot establish resource scope or delegation lineage.

Required direction:
- Child grants must be strict subsets of parent actions, resources, audience, and expiry.
- Reject grants that add permissions, widen resource patterns, extend expiry, or change tenant.
- Persist parent grant ID and delegation depth; cap depth and total fan-out.
- Require explicit policy for which actions may be delegated and which must remain owner-only.
- Ensure approvals cannot be delegated or replayed as general-purpose capabilities.

### P1 — Revocation and policy drift are not checked through one execution-time mechanism

MCP policies carry policy versions and registry enablement is refreshed before planning/dispatch. Workflow leases expire and are fenced. However, the reviewed task execution contract does not bind a grant revocation epoch or mandate revalidation against current agent manifest and policy state at each protected action.

Risk: disabling an agent, rotating its manifest, changing resource policy, or revoking a delegated grant may stop future routing but does not by itself prove that already queued or running work loses authority immediately.

Required direction:
- Maintain grant status/revocation epoch in a trusted control-plane store.
- Check revocation and current policy revision on each privileged operation; use short TTLs to bound stale cache exposure.
- Define explicit active-task behavior: immediate cancellation, stop-before-next-side-effect, or bounded drain; high-risk actions should fail closed on revocation-store unavailability.
- Revoke descendants when a parent grant is revoked.
- Record revocation reason and affected task IDs without storing bearer tokens.

### P1 — User approval is not equivalent to a scoped execution grant

ApprovalCoordinator consumes an actor/session-bound pending action and records approval before calling execute_approved_action. That is a strong one-time consent flow, but the approval record does not establish a worker identity, tenant, manifest revision, downstream resource scope, or current grant revocation state.

Risk: downstream execution must not interpret a successful user confirmation as blanket authorization for arbitrary operations by the agent. Approval and execution authority are separate requirements.

Required direction:
- Bind approval to a canonical action digest, target resource, agent/manifest, task, and policy revision.
- At execution, verify both fresh user approval and a valid attenuated execution grant.
- If action parameters or resource targets change, require a new approval.
- Revalidate actor/session validity at the final side-effect boundary where practical.

### P2 — Resource-level constraints are not first-class in specialist permission declarations

SpecialistAgent permissions are strings such as web:read or artifact:write. These express action classes but not the exact resource, tenant, path, domain, account, record, or data classification allowed.

Risk: an agent authorized for artifact:write could be over-broad if downstream code does not add its own path/resource restrictions.

Required direction:
- Model action and resource separately.
- Use structured constraints (exact IDs, bounded prefixes, approved hosts, account IDs, data classes) rather than free-form wildcard strings.
- Resolve and canonicalize resources before authorization; enforce after canonicalization to avoid path/URL aliasing.
- Default to deny when resource identity cannot be established.

## Canonical execution grant proposal

A grant should be an immutable signed envelope with at least:

- grant_id, issuer, subject, audience
- owner_id / tenant_id and initiating_principal_id
- agent_id and manifest_digest
- task_id and authenticated_worker_principal_id
- actions and structured resource constraints
- parent_grant_id and delegation_depth
- policy_revision and revocation_epoch
- issued_at, not_before, expires_at
- approval_id/action_digest where human approval is required
- signing_key_id and schema_version

The issuer must derive these claims from trusted server-side records, not accept arbitrary model-proposed claims. The verifier must use a trusted key set, reject unknown schema versions, enforce strict attenuation, and consult revocation/policy state. The grant is a proof of bounded authority, not a replacement for the worker's authenticated identity.

## Recommended implementation sequence

1. Define the typed grant schema and threat model with explicit issuer/verifier trust boundaries.
2. Implement key management and signed-envelope verification with key rotation and revocation.
3. Add owner/tenant and manifest revision references to durable plans/tasks.
4. Issue a task-scoped grant during authorized dispatch and persist only grant metadata/digest, not bearer material.
5. Bind worker authentication to grant subject and audience.
6. Enforce grant plus resource constraints in the lowest-level side-effect adapters.
7. Add parent-child attenuation, revocation epochs, and bounded active-task drain semantics.
8. Bind one-time human approvals to the exact grant/action/resource digest.
9. Add adversarial tests for forged claims, wrong audience, privilege widening, expired/revoked grants, stale policy versions, cross-tenant replay, and parent revocation.

## Validation and limitations

This audit is based on source inspection of the paths listed above. No tests were executed and no signed-grant prototype was deployed. No live signature forgery, key compromise, cross-tenant exploit, or policy-revocation race was demonstrated.

No runtime patch was submitted. A new token helper without end-to-end verification at all side-effect boundaries would create a false security boundary. Implement this as a coordinated control-plane milestone, with compatibility and migration tests across task runtime, specialist coordinator, MCP, approvals, and workers.
