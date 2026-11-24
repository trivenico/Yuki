# Audit 091 — Yuki Control Plane, Agent Registry, and Centralized Policy Architecture

Repository: trivenico/Yuki  
Base: main  
Scope: specialist registry/coordinator, durable task runtime/store, authentication/permissions, ASGI runtime composition.  
Outcome: architecture audit; no runtime patch submitted.

## Executive summary

Yuki has several useful control-plane building blocks, but the inspected code does not yet demonstrate one integrated, authoritative control plane governing every specialist agent.

The strongest current component is SpecialistAgentCoordinator: it maintains a durable SQLite specialist registry, validates bounded capability/permission manifests, routes by least over-provisioned capability match, supports authorized registration and enable/disable, and refreshes registry state across coordinator instances. The task runtime separately provides durable tasks, worker leases, approval gates, and versioned event history. Built-in authentication separately supplies user/session identity and coarse role permissions.

These are valuable foundations, but they are separate services with caller-supplied authorization callbacks and no common first-class agent identity/delegation contract in the inspected integration. The ASGI runtime builder composes conversation, auth, inference, OAuth, MCP, and evaluation APIs, but does not accept or expose the specialist coordinator in the reviewed signature. Consequently, the reviewed paths do not establish a single authenticated API/control-plane boundary for registry management, planning, dispatch, worker execution, and policy revalidation.

This is an architectural integration gap, not proof that no other integration exists elsewhere in the repository or that a live endpoint is exploitable.

## Files inspected

- automation/agent_coordinator.py
- automation/task_runtime.py
- automation/workflow_store.py
- core/auth.py
- core/permissions.py
- api/runtime.py
- automation/inference_worker_supervisor.py
- tests/test_agent_coordinator.py

## Positive controls observed

1. SpecialistAgent manifests are bounded and validated: agent ID, name, action, capabilities, permissions, and enabled state.
2. Specialist registration, enable/disable, listing, planning, dispatch, reading, and cancellation use explicit authorization callback actions and fail closed unless the callback returns exactly True.
3. Registry state is persisted in SQLite. Registry refresh reads the database as source of truth, allowing another coordinator instance's changes to become visible.
4. Routing is deterministic and selects the least over-provisioned enabled specialist, with stable agent ID tie-breaking.
5. Agent plans persist specialist assignment, capability requirements, permission requirements, dependency graph, approval requirement, and task linkage.
6. Dependency outputs are bounded and explicitly framed as untrusted data.
7. Workflow task runtime provides durable state, atomic claims, lease ownership, approval gates, compare-and-swap updates, and task event history.
8. Built-in authentication uses opaque sessions with stored token digests and maps authenticated users to coarse role permissions.
9. ASGI runtime composition derives API owner identity from the authenticated session rather than caller-provided configuration.
10. Agent coordinator and task runtime are deliberately separate from direct model/tool execution; coordinator dispatches task definitions while workers execute them.

## Findings

### P1 — The reviewed application composition does not establish one central agent control-plane boundary

SpecialistAgentCoordinator is constructed separately with a TaskRuntime, database path, specialist definitions, and an injected authorization callback. The reviewed build_conversation_runtime composes authenticated conversation, inference, OAuth, MCP, and evaluation APIs, but its signature does not accept the specialist coordinator or an agent-control-plane service. The reviewed API composition therefore does not show a unified authenticated boundary for agent registration, registry administration, planning, dispatch, cancellation, and audit.

Risk: different application entry points or deployment compositions may expose different subsets of control-plane functions and apply different authentication, authorization, lifecycle, and audit rules.

Required direction: introduce a dedicated AgentControlPlane service as the sole application-facing facade. It should own authenticated principal resolution, tenant/owner scope, agent registry, plan lifecycle, policy evaluation, worker identity binding, audit emission, and task dispatch. Expose it through explicit authenticated API routes and compose it in the application runtime. Do not let API handlers or agents instantiate their own registries/coordinators.

### P1 — Agent registry manifests describe routing metadata, not enforceable agent identity or runtime authority

SpecialistAgent stores an agent ID, display name, capabilities, action, permissions, and enabled flag. These values guide routing, but the manifest is not a signed workload identity and does not itself prove which runtime process is executing the task. A task records its worker lease owner, but the reviewed specialist manifest and worker lease are not joined by a verifiable principal/agent/task/tenant grant.

Risk: registry metadata can be mistaken for execution authorization. A selected specialist name is not proof that the worker is the authorized agent or that its actual tool permissions are limited to the manifest.

Required direction: define immutable agent identity plus versioned manifest digest; issue short-lived task-scoped workload grants binding issuer, subject agent, worker principal, task, owner/tenant, audience, allowed actions/resources, expiry, policy revision, and revocation epoch. Verify the grant at every side-effect boundary.

### P1 — Authorization policy is injected as independent callbacks, not a shared policy decision contract

The coordinator's authorize callback receives actor, action, and resource strings. TaskRuntime has its own authorization callback and auth service has a separate coarse role-to-permission mapping. This allows useful fail-closed checks, but the inspected contracts do not share a normalized principal, tenant, resource attributes, agent identity, delegation chain, policy version, or decision evidence.

Risk: two layers can disagree about whether a user or agent may perform an action. Policy changes may not be consistently revalidated between planning, dispatch, approval, and execution.

Required direction: centralize policy evaluation behind a typed policy decision point. Include principal type, user/tenant, agent, worker, action, resource, risk, delegation, approval, and policy revision. Return a structured decision with reason and expiry; revalidate at dispatch and side-effect execution. Keep policy enforcement in each resource-owning service rather than relying only on API gateway checks.

### P1 — Registry changes are refreshed but not version-pinned through plan dispatch

The coordinator refreshes its local registry before planning and before dispatch, and plans persist the selected agent ID and required capability/permission sets. The reviewed plan record does not pin a registry revision or manifest digest. A registry change can occur after refresh and before task enqueue; the dispatch path uses its refreshed in-memory agent snapshot rather than a transactional registry revision check tied to task creation.

Risk: a plan can be dispatched using a stale enablement or manifest snapshot during a concurrent registry update. A later audit cannot reliably reconstruct the exact manifest revision used at planning and dispatch.

Required direction: add monotonically increasing registry revision and immutable manifest digest. Persist both in each planned subtask. At dispatch, transactionally verify the selected agent remains enabled and the manifest/revision satisfies the stored policy. If changed, mark the plan for re-planning or explicit approval rather than silently using stale metadata.

### P2 — Registry and plan ownership/tenant boundaries remain incomplete in the reviewed schema

The specialist registry is global in the inspected schema. Agent plan records contain plan ID, idempotency key, request digest, and timestamp, without owner or tenant columns. Authorization callbacks receive plan ID as a resource, but the records cannot independently enforce owner/tenant scoping.

This overlaps earlier identity and orchestration audits; it remains a blocking prerequisite for hosted multi-user or multi-tenant operation.

Required direction: persist owner/tenant on registry entries, plans, subtasks, and task links. Enforce composite ownership in SQL reads and writes, and use tenant-scoped uniqueness for idempotency keys. Derive owner/tenant from authenticated principal, never request body.

### P2 — Agent lifecycle, health, and capacity are not unified in the registry

The registry persists enabled/disabled state and routing manifests, while worker supervisor health is a separate process-local lifecycle component. The reviewed registry does not persist heartbeat, worker instance, readiness, capacity, model availability, queue depth, or circuit state.

Risk: a specialist can be enabled and selected despite its worker being unavailable, unhealthy, overloaded, or missing a required runtime dependency.

Required direction: separate desired state (registered/enabled policy) from observed state (heartbeat/readiness/capacity/health). Use expiring worker leases and health reports from authenticated workers. Route only to ready capacity while preserving policy constraints; never let health reports grant permissions.

### P2 — No common control-plane audit envelope spans registry, planning, policy, dispatch, and execution

Task events and approval audit have their own persistence contracts. The inspected coordinator stores plans and subtasks, but no common event envelope was demonstrated that ties actor, tenant, agent manifest revision, policy decision, approval, task, worker principal, lease epoch, and final side effect together.

Risk: security review and incident response cannot reliably reconstruct the complete decision chain across services.

Required direction: emit a common metadata-only audit envelope with correlation IDs and immutable references to policy/manifest revisions. Do not log prompts, tool arguments, secrets, or raw provider output by default.

## Target control-plane architecture

1. Identity: authenticated human, service, agent, and worker principals with tenant scope.
2. Agent registry: versioned immutable manifests, desired state, capabilities, declared permissions, runtime requirements, and signed revisions.
3. Policy decision point: one typed authorization contract; enforcement remains at every service/resource boundary.
4. Planner/router: deterministic capability matching, health/capacity-aware scheduling, tenant quotas, and policy-constrained routing.
5. Task control: durable task ledger, owner/tenant binding, approval state, lease epoch, retries, and cancellation.
6. Worker plane: authenticated workload identity, short-lived attenuated task grants, isolated execution, and bounded resources.
7. Observability: unified audit correlation, health/heartbeat, queue metrics, policy decisions, and incident trail.
8. Admin API: explicit authenticated routes for registry lifecycle, policy revisions, quotas, task operations, and emergency disable/revoke.

The 100 specialist agents should be declarative entries managed by this control plane—not 100 separately privileged services.

## Recommended implementation order

1. Define principal, tenant, agent, worker, and task identity contracts.
2. Build the AgentControlPlane facade and compose it into the authenticated application runtime.
3. Add owner/tenant columns and ownership enforcement to registry/plans/tasks.
4. Add registry revision and manifest digest pinning.
5. Introduce typed policy decision and task-scoped signed workload grants.
6. Add worker readiness/capacity heartbeat and safe routing.
7. Standardize cross-service audit correlation.
8. Add multi-tenant integration, concurrent registry update, policy revocation, and worker impersonation tests.

## Validation and limitations

This audit is based on source inspection of the files listed above. No tests were executed, no hosted multi-tenant deployment was inspected, and no live control-plane exploit was demonstrated. The absence of an integration in the reviewed runtime signature is not proof that no alternate integration exists elsewhere in the repository.

No runtime patch was submitted. A local registry change would not establish a centralized control plane without the identity, tenant, policy, task, worker, and API contracts being introduced together.
