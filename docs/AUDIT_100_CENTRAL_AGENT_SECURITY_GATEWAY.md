# Audit 100 — Central Agent Security Gateway & Unified Authorization

**Repository:** trivenico/Yuki  
**Base inspected:** main  
**Scope:** runtime composition, human authentication, specialist orchestration, tool dispatch, approvals, MCP policy and task execution.

## Executive summary

Yuki has several strong security subsystems, but the reviewed composition does not establish one mandatory authorization gateway across all agent execution surfaces. Identity, policy and approval are distributed among API wrappers, injected callbacks, tool runtimes, MCP policy services and task services.

The principal architectural risk is policy fragmentation: a control may be correctly enforced in one path while another path uses a weaker contract or is not composed through the same boundary. The 100-agent design should not depend on every specialist independently remembering to call the right authorization callback.

This audit recommends a central security gateway as a mandatory policy decision and enforcement boundary. It does not recommend a single giant service or bypassing existing specialized controls. The gateway should compose identity resolution, policy evaluation, approval verification, capability/delegation checks, audit and revocation while each resource-owning subsystem retains final enforcement.

## Reviewed source

- api/runtime.py
- core/auth.py
- core/permissions.py
- core/guarded_tool_runtime.py
- core/approval_flow.py
- core/pending_approvals.py
- automation/agent_coordinator.py
- automation/task_runtime.py
- automation/workflow_store.py
- core/mcp_runtime.py

## Findings

### P1 — No mandatory central security gateway in application composition

The reviewed build_conversation_runtime composes authenticated conversation, evaluation, inference, income, OAuth and MCP API wrappers. It does not accept or expose SpecialistAgentCoordinator, TaskRuntime, or a common authorization gateway. Those subsystems are constructed separately and use their own injected authorization callbacks.

This means the reviewed composition does not prove that specialist planning, dispatch, task execution and tool invocation all share a single principal/policy/revocation contract.

**Recommendation:** introduce an explicit AgentSecurityGateway dependency in the application composition root. It must be required for all agent orchestration and protected side effects. Avoid optional permissive defaults. Keep API session authentication as the source of human principal identity; never accept principal identity from model output or user-controlled tool arguments.

### P1 — Authorization contracts are inconsistent across enforcement points

TaskRuntime uses an actor/action/task callback and fails closed when missing or when callback output is not literal True. SpecialistAgentCoordinator uses a separate actor/action/resource callback. GuardedToolRuntime calls authorize_tool(name), which does not itself receive an authenticated principal, tenant, resource, task, delegation grant or policy revision.

The GuardedToolRuntime literal-True hardening is already proposed in PR #1144; the patch is open and not merged at the time of this audit. It must not be treated as present on main.

**Recommendation:** normalize decisions into a typed policy contract with explicit principal, tenant, agent/workload identity, action, resource, task, risk, delegation grant, approval reference and policy revision. Every enforcement point must require an explicit allow decision; malformed, missing, stale or exceptional decisions deny.

### P1 — Tool authorization is not principal- or resource-aware at the generic CLI boundary

GuardedToolRuntime authorizes only by tool name. A name-only policy can distinguish registered tool labels but cannot independently express who is invoking it, for which resource, under which task, with what delegated authority, or at which policy revision.

**Recommendation:** add a principal-bound invocation context and immutable typed tool descriptor. Check resource/action scopes and current revocation at the point of dispatch, not only when a plan is created. Do not retrofit identity by trusting an actor string supplied by the model.

### P1 — Approval, policy and execution are not yet one verifiable authorization decision

ApprovalCoordinator provides valuable actor/session binding, one-time consumption and audit behavior. MCP has a separate owner/provider/account/tool/risk/scope/revision policy and confirmation path. The reviewed interfaces do not establish one common authorization envelope linking principal, agent, tenant, task, tool/resource, policy revision, approval and final side effect.

**Recommendation:** use an authorization decision record with a stable decision ID. For sensitive actions, bind the approval to the exact action digest and resource scope, then revalidate live session, grant, policy revision and revocation immediately before execution. A successful approval must not become a reusable general-purpose permission.

### P1 — Delegated workload authority is not yet enforced by the common gateway

The specialist registry contains routing capabilities and permission labels, but those are not cryptographic proof of the executing workload's identity. Prior audits identified that worker IDs are labels unless bound to authenticated workload credentials, and that task-scoped signed attenuating grants are not yet demonstrated.

**Recommendation:** the gateway should verify short-lived workload credentials and signed execution grants bound to issuer, subject, audience, tenant, task, resource/action scope, expiry, policy revision and revocation epoch. A child agent grant must be a strict subset of its parent grant.

### P2 — Audit correlation is fragmented across security subsystems

Approval audit, MCP policy audit and task event streams have distinct records. The reviewed paths do not establish a common correlation identifier spanning identity resolution, policy decision, approval, dispatch, worker claim, tool call and side effect.

**Recommendation:** emit a common, privacy-preserving security event envelope with decision ID, actor/workload IDs, tenant, task, resource/action, policy revision, approval reference, outcome and timestamp. Never place bearer credentials, raw prompts, tool secrets or sensitive arguments in the audit envelope.

### P2 — Revocation and policy freshness need a common contract

The registry, MCP policy store, pending approvals, task leases and API sessions have different lifecycles. The reviewed composition does not show one revocation signal or policy epoch that every agent worker and tool boundary checks.

**Recommendation:** centralize policy versioning and revocation events, but require local enforcement points to fail closed when policy state is stale or unavailable for high-risk operations. Define bounded cache lifetimes and emergency deny behavior.

## Existing positive controls to preserve

- Human API identity is derived from authenticated sessions in the API layer.
- TaskRuntime authorization fails closed and requires literal True.
- ApprovalCoordinator binds approvals to actor/session and consumes them once.
- MCP policy binds owner/provider/account/tool/risk/scopes/version.
- Specialist manifests are bounded and validated; routing is deterministic.
- Task state uses durable records, leases, optimistic versions and explicit approval gates.
- Guarded tool dispatch bounds arguments/results and does not expose handler exceptions.

## Recommended target architecture

1. **Identity resolver:** converts authenticated human sessions and verified workload credentials into immutable principals. No model-generated identity.
2. **Policy decision point:** evaluates principal, tenant, agent, action, resource, task, risk, grant, approval and policy revision.
3. **Policy enforcement points:** API, coordinator, task runtime, MCP runtime, skill runtime and tool dispatch independently enforce decisions at their resource boundary.
4. **Delegation verifier:** validates signed, attenuating grants and revocation.
5. **Approval verifier:** checks one-time approval bound to exact action digest, actor/session and expiry.
6. **Audit recorder:** records correlated decision and execution lifecycle metadata without secrets.
7. **Revocation/version service:** publishes policy epochs and invalidates sessions, grants, approvals and worker credentials.

The gateway must be a required dependency for protected actions. It must not become a broad bypass token or a single boolean callback with no structured context.

## Phased implementation plan

### Phase 1 — Normalize authorization contract
Define typed Principal, WorkloadIdentity, AuthorizationRequest, AuthorizationDecision and DenialReason models. Add contract tests for missing, malformed, stale and exceptional decisions.

### Phase 2 — Enforce at existing boundaries
Adapt API composition, SpecialistAgentCoordinator, TaskRuntime, GuardedToolRuntime and MCPRuntimeService. Preserve specialized approval and owner-scoped checks; ensure the gateway decision is necessary but not sufficient to bypass local resource ownership checks.

### Phase 3 — Bind identity and delegation
Add verified workload identity and signed attenuating execution grants. Persist tenant/owner/task binding and policy revision through planning, dispatch, claim and side-effect execution.

### Phase 4 — Correlated audit and revocation
Add decision IDs, policy epochs, revocation propagation, emergency deny, and integration tests across two owners and multiple workers.

### Phase 5 — Production verification
Run multi-process concurrency tests, cross-tenant negative tests, worker impersonation tests, stale-policy tests, approval replay tests, tool boundary tests and chaos tests for policy-service outages. Do not claim production readiness based on unit tests alone.

## Implementation and validation status

- This PR is architecture/audit documentation only. A central gateway was not added because a superficial wrapper would not secure the existing execution boundaries.
- The explicit-boolean GuardedToolRuntime remediation is separately proposed in PR #1144 and is not merged into main.
- Actor-scoped specialist plan idempotency is separately proposed in PR #1174 and is not merged into main.
- Source inspection only. Tests were not executed in this audit.
- No PR was merged and no deployment was performed.

## Conclusion

Yuki has useful security primitives, but it needs one coherent authorization contract enforced at every protected boundary. The next engineering phase should implement the typed gateway incrementally, migrate real call sites, and prove isolation with adversarial integration tests before expanding the agent fleet.
