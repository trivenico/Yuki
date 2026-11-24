# Audit 099 — Agent Identity Isolation & Secure Agent-to-Agent Delegation

**Repository:** trivenico/Yuki  
**Base inspected:** main  
**Scope:** specialist registry, planning, task dispatch, authorization callbacks, workflow/task ownership, delegation boundaries.

## Executive summary

The specialist coordinator authorizes plan creation through an injected callback and stores plans in SQLite. However, the plan idempotency key is globally unique and was derived only from caller-provided key. The plan ID was also derived only from that key. Because the plan row has no owner or tenant field, a second authorized actor reusing the same idempotency key and submitting the same canonical plan could receive the first actor's persisted plan.

This mirrors the cross-actor task idempotency problem identified in Audit 098, but exists independently in the specialist plan layer.

Implementation PR #1174 scopes the internal plan idempotency key and deterministic plan ID to the authenticated actor, preserving same-actor retries while preventing cross-actor key collisions.

## Reviewed source

- automation/agent_coordinator.py
- automation/task_runtime.py
- automation/workflow_store.py
- core/permissions.py
- core/approval_flow.py
- core/pending_approvals.py
- core/agent.py
- core/agent_execution_loop.py
- core/mcp_runtime.py
- tests/test_agent_coordinator.py
- tests/test_task_runtime.py
- tests/test_agent_task_journal.py

## Findings

### P1 — Global agent-plan idempotency namespace crosses actors

SpecialistAgentCoordinator.submit authorizes actor_id, but stores idempotency_key in a globally UNIQUE agent_plans column. The deterministic plan ID was also derived solely from the caller key. If another actor submitted the same key and same canonical plan, the existing plan was returned. The plan schema does not persist owner_id or tenant_id.

**Remediation submitted:** derive internal idempotency key from authenticated actor ID plus caller-provided key using SHA-256; derive plan ID from that scoped key. Same-actor retries remain idempotent; cross-actor key reuse produces a distinct plan.

Regression tests cover cross-actor plan key reuse and same-actor retry. Tests added but not executed.

### P1 — Plan records do not intrinsically bind owner/tenant

agent_plans persists plan_id, idempotency_key, request_digest and timestamp, while subtasks persist agent assignment and execution metadata. There is no creator principal or tenant field. Authorization callbacks gate operations, but the database cannot independently enforce owner/tenant scope or filter plan reads by owner.

**Recommendation:** persist tenant_id, owner_id, creator principal and authorization domain on plan rows. Enforce principal predicates in read/dispatch/cancel operations. Add tenant-scoped idempotency uniqueness through a versioned migration and explicit ownership reconciliation for existing plans.

### P1 — Specialist manifest is routing metadata, not authenticated workload identity

SpecialistAgent contains ID, name, capabilities, action, permissions and enabled state. The registry does not prove that the executing process is the registered agent. A worker label is not cryptographic proof of workload identity.

**Recommendation:** issue short-lived workload credentials from the control plane, bind them to agent ID, manifest digest, task, audience, tenant, allowed actions, expiry and revocation epoch. Verify them at worker claim and each side-effect boundary.

### P1 — Delegation is not yet represented as an attenuating grant

The coordinator checks capability/permission requirements during planning and stores assignments. The reviewed task/worker path does not carry a signed grant proving that a child agent's authority is a subset of the delegator's authority.

**Recommendation:** implement signed, attenuating grants with issuer, subject, audience, tenant, task/resource scope, actions, expiry, policy revision and unique token ID. Child grants must never widen parent scopes or expiry. Revalidate revocation and policy version at execution.

### P1 — Task progress and worker identity remain separate concerns

TaskRuntime progress authorization is actor/action/task based. AgentTaskJournal adds worker lease checks in its adapter, but worker IDs remain labels unless backed by authenticated workload credentials. Direct runtime callers need the same store-enforced lease/identity validation.

### P2 — Agent-to-agent communication lacks a universal authenticated message contract

Task events provide sequence and actor labels, but do not constitute a general signed recipient-addressed message envelope with sender/recipient identity, tenant, delegation grant, provenance, replay protection and acknowledgement lifecycle.

### P2 — Shared registry and orchestration ownership remain global

The specialist registry is global in the reviewed coordinator database. Registry administration is gated by callbacks, but tenant-scoped registries, lifecycle policy and manifest revision pinning through plan dispatch are not established by these reviewed paths.

## Positive controls

- Specialist manifests are bounded and validated.
- Registration and enable/disable operations require explicit authorization.
- Routing is deterministic and uses declared capabilities/permissions.
- Plan submission validates dependencies and cycles.
- Dispatch is separate from planning and task workers.
- Task runtime uses durable state, version checks, worker leases and approval gates.
- Approval records bind actor/session and are one-time consumed.

## Implementation status

- PR #1174 contains actor-scoped plan idempotency and regression tests.
- Audit documentation is submitted separately in PR #1175.
- Tests added but not executed.
- No signed workload identity, delegation token, multi-tenant deployment, or live impersonation test was implemented/performed.
- No PR merged or deployed.

## Target architecture

1. Root owner and control plane issue principal-bound workload identities.
2. Each specialist has a stable identity, immutable manifest revision and isolated data namespace.
3. Every task dispatch carries a signed, attenuated execution grant.
4. Workers authenticate cryptographically; worker IDs are descriptive labels only.
5. Every tool/resource boundary verifies audience, scope, expiry, policy revision and revocation.
6. Agent-to-agent messages carry authenticated sender, recipient, tenant, task, provenance and replay identifiers.
7. Collaboration is an explicit, auditable, revocable grant—not inherited access.

## Conclusion

A concrete cross-actor plan collision was found and a focused fix submitted. The larger milestone remains the transition from callback-gated planning metadata to authenticated, tenant-aware, cryptographically scoped agent execution.
