# Audit 079 — Agent Authorization, Capability-Based Security, and Policy Enforcement

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Scope:** tool dispatch authorization, skill approval gates, autonomy tiers, durable approvals, MCP policy, and agent execution.

## Executive summary

Yuki has multiple useful controls: tool handlers are explicitly registered, the guarded tool runtime consults a policy before dispatch, policy exceptions deny, autonomy tiers do not silently bypass approvals, one-shot skill approvals bind to exact argument digests, durable approval records bind actor and session, and MCP policies use owner/provider/account/tool/risk/scope context with durable audit.

The authorization model remains distributed across several layers. The generic CLI tool runtime authorizes by tool name only; the guarded skill runtime explicitly does not sandbox skill code; and the durable approval coordinator depends on the caller to provide trusted actor/session identity. There is no single demonstrated policy decision contract that consistently binds principal, agent, tenant, delegation, resource, action, risk, and policy revision at every side-effect boundary.

## Confirmed finding and fix

### P1 — Guarded tool runtime accepted truthy non-boolean policy decisions

In `core/guarded_tool_runtime.py`, the policy response was tested using Python truthiness. A malformed or incorrectly implemented policy returning a non-empty string, integer, list, or arbitrary object could therefore be treated as authorization success.

**Fix submitted:** require `authorized is True`; all other values deny. Policy exceptions continue to deny.

**Regression test added:** policy decisions returning a string, integer, object, or list must not execute the registered handler.

Tests have not been executed.

## Existing positive controls

- Tool names must be registered and handlers callable.
- Policy exceptions fail closed.
- Tool arguments and returned text are bounded.
- Autonomy tier 0 blocks state changes.
- State-changing and external actions require fresh explicit approval at every tier.
- Tier changes themselves require approval.
- One-shot skill approvals bind to exact argument digests and are thread-safe.
- Durable approval requests snapshot JSON-safe action arguments and bind actor/session.
- Pending approvals are consumed once and audit failure prevents execution.
- MCP tool definitions are host-reviewed and include risk and required scopes.
- MCP policy storage is durable and owner/provider/account scoped.

## Additional findings and architecture gaps

### P1 — Generic tool authorization is name-only

The `GuardedToolRuntime` policy interface receives only a tool name. It does not receive an authenticated principal, agent identity, tenant, resource, action arguments, risk classification, delegation grant, or policy revision. This is insufficient as the sole authorization interface for a multi-agent system. Keep it as a low-level dispatch guard only; place it behind a richer authorization service.

### P1 — Skill runtime is not a sandbox

`core/guarded_skill_runtime.py` explicitly states that it does not sandbox skill code. Approval determines whether an invocation may proceed, but does not contain the privileges of the code once executed. Untrusted or third-party skills require a separate process/container/VM boundary with restricted filesystem, network, credentials, and resource limits.

### P1 — Durable approval trusts caller-supplied identity

`ApprovalCoordinator.record/approve` accepts actor and session identifiers as arguments. The coordinator binds pending state to these values, but it does not itself resolve the live session or verify that the principal still has permission at execution time. The caller must supply identity from a trusted authentication boundary and revalidate authorization immediately before side effects.

### P1 — No common capability/delegation token contract

The inspected generic authorization and approval APIs do not demonstrate a common signed, short-lived, attenuating grant that binds issuer, subject agent, audience, tenant, permitted tools/actions, resource scope, expiry, policy revision, and revocation. Build this as a first-class capability model rather than passing role labels or free-form tool-name lists.

### P2 — Policy freshness and revocation need a shared contract

MCP has provider/tool policy versioning concepts, but a unified policy revision and immediate revocation contract across generic tools, skills, agents, background jobs, memory, and integrations was not demonstrated.

### P2 — Audit is not uniformly a hard authorization prerequisite

Some approval paths fail closed when their durable audit append fails. Generic tool audit callback failures are swallowed. Define risk-tiered requirements: high-risk side effects must have durable pre-execution authorization/audit records, while best-effort telemetry must never be mistaken for the security audit trail.

### P2 — Argument/resource authorization needs canonicalization

Authorizing only tool names cannot express whether a given argument targets an allowed file, account, tenant, integration, or resource. The central policy service should authorize a canonical action descriptor and enforce the same descriptor at dispatch to prevent check/use drift.

## Recommended architecture

1. Introduce a central `AuthorizationDecision` service that receives authenticated principal, agent identity, tenant, action, canonical resource, risk, delegation grant, and policy revision.
2. Issue short-lived signed capability grants with explicit audience, subject, allowed action/resource scopes, expiry, issuer, and revocation identifier.
3. Make delegation privilege-attenuating: child agents may receive only a subset of parent capabilities.
4. Revalidate capabilities and resource ownership at the final side-effect boundary, not only at planning or approval time.
5. Separate approval from execution isolation. Run untrusted skills and plugins in restricted workers.
6. Require durable pre-execution audit for high-risk actions and distinguish it from optional telemetry.
7. Add cross-agent adversarial tests for forged identities, cross-tenant resource IDs, stale policy versions, revoked capabilities, privilege escalation through delegation, and concurrent approval replay.

## Validation limits

Source inspection only. Regression tests were added but not executed. No multi-tenant deployment, capability signing/verification service, concurrent revocation test, sandbox escape test, or full side-effect integration test was run. This audit does not claim a complete zero-trust implementation or production readiness.
