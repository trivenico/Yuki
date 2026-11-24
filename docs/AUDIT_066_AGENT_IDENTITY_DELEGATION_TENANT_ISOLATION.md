# Yuki Engineering Audit 066 — Agent Identity, Delegation, and Multi-Tenant Isolation

**Review date:** 2026-11-24  
**Repository:** `trivenico/Yuki`  
**Review type:** Targeted source and test inspection  
**Status:** Documentation audit plus one implementation PR for a confirmed ambiguous-authentication-header defect. No tests executed.

## Executive assessment

Yuki has a useful single-user authentication foundation: opaque random bearer sessions, hashed session tokens at rest, expiry and revocation checks, disabled-account invalidation, role-derived permissions, owner-scoped resource stores, and approval records bound to actor/session. OAuth authorization uses a stricter duplicate-header parser that requires exactly one Authorization header.

The inspected built-in auth schema models users and sessions, not organizations, tenant memberships, agent identities, or delegated grants. Existing owner-scoped access is valuable, but it is not equivalent to organization-level multi-tenancy. The audit did not find a complete first-class tenant membership and role boundary in the inspected authentication and API composition code.

A concrete inconsistency was confirmed: `AuthenticatedOAuthConnectionAPI._user()` converted request headers to a dictionary, allowing duplicate Authorization headers to collapse to whichever value was encountered last. The parallel OAuth authorization API already rejects duplicate Authorization headers. A separate implementation PR now makes the connection API reject ambiguous credentials and adds a regression test.

## 1. Built-in user and session identity

### `core/auth.py`

Observed:
- User accounts have opaque user IDs, unique usernames, password hashes, roles, disabled state, and login lockout fields.
- Sessions store SHA-256 token digests rather than bearer secrets.
- Session resolution checks token digest, revocation, expiry, and disabled-account status.
- Session revocation and account disablement revoke active sessions.
- Roles map to a fixed permission set: admin, operator, user.

Important boundary:
- This is a user/session identity service. Its inspected schema contains `auth_users` and `auth_sessions`; it does not define organizations, tenant memberships, tenant roles, service principals, or agent identities.
- A role is global to the account in this model. It does not encode per-tenant membership or resource scope.
- Do not interpret `user_id` owner scoping as proof of a multi-organization tenant authorization model.

Recommendations:
1. Introduce explicit tenant and membership records before advertising multi-tenant organization support.
2. Resolve active tenant from authenticated server-side membership and resource context; never accept tenant authority from model output or arbitrary request JSON.
3. Define role/capability grants per tenant and resource, with default-deny behavior.
4. Keep user, agent, service, and delegated identities distinct.
5. Add membership revocation/versioning so stale requests and long-running jobs can be fenced.

## 2. OAuth connection API — confirmed defect and fix

### `api/oauth_connections.py`

Observed:
- Connection listing and removal require an authenticated session.
- Registry operations are scoped to `user.user_id`.
- Responses expose connection metadata, not credential material.
- Disconnect verifies the owner-scoped record before calling a trusted provider service.

Confirmed defect:
- `_user()` built a dictionary from ASGI headers. Duplicate Authorization entries were silently collapsed, creating ambiguity about which bearer credential the application would authenticate.
- `api/oauth_authorization.py` already uses a safer approach: it collects Authorization values and requires exactly one.

Implementation submitted:
- The connection API now collects Authorization header values and rejects requests unless exactly one valid bytes-valued header is present.
- A regression test submits two Authorization headers and verifies authentication fails before owner data is returned.

Implementation PR: https://github.com/trivenico/Yuki/pull/1123

Remaining:
- Apply one shared, tested ASGI authentication-header parser consistently across every authenticated API wrapper.
- Verify reverse proxies and ASGI servers preserve/reject duplicate credentials consistently; application rejection is the final safety boundary.
- Continue owner checks for every read, mutation, disconnect, export, and asynchronous operation.

## 3. OAuth authorization and identity binding

### `api/oauth_authorization.py`

Observed:
- Session extraction requires exactly one Authorization header.
- Provider IDs resolve through trusted host configuration; request data cannot supply an arbitrary provider URL.
- Authorization start and callback bind the operation to the authenticated user and session.
- Callback body shape is constrained and callback failures avoid echoing authorization codes.
- Provider mismatch and connection registration failures have compensating behavior.

Recommendations:
1. Keep provider identity, owner identity, account identity, and tenant identity separate.
2. Ensure OAuth state, PKCE, nonce, callback one-time consumption, and session binding are verified in the underlying provider service for every provider.
3. If organizations are introduced, bind connection ownership to a tenant plus the initiating user and explicit sharing policy.
4. Define what happens when tenant membership is revoked while an OAuth flow is pending.
5. Test account switching, callback replay, cross-user callback substitution, and membership revocation during callback.

## 4. Agent identity and delegation

### Inspected paths
- `core/agent_execution_loop.py`
- `core/agent.py`
- `core/permissions.py`
- `core/tiered_autonomy.py`
- `core/approval_flow.py`
- `core/pending_approvals.py`
- `core/guarded_tool_runtime.py`
- `core/agent_registry.py`, `core/agent_identity.py`, `core/delegation.py`, `core/agent_delegation.py`, and `core/orchestration/{registry,delegation,state}.py` were not present at the inspected paths.

Observed:
- The inspected execution loop accepts a model callable and a guarded tool protocol. It does not itself establish a cryptographic or durable agent principal.
- Generic permission checks are callback-based and do not inherently carry tenant, agent, resource, or delegated-grant claims.
- Tiered autonomy is a policy descriptor; it does not itself create a secure delegation mechanism.
- No first-class agent identity/delegation module was found at the candidate paths inspected. This is a bounded inventory result, not proof that no agent-specific identity exists anywhere in the repository.

Recommendations:
1. Assign immutable agent IDs and distinct agent credentials or workload identities.
2. A delegated task must carry a signed/verified grant containing issuer, subject agent, tenant, allowed capabilities, resource scope, task ID, expiry, audience, policy revision, and parent delegation ID.
3. Delegation must attenuate privileges: child grants may only narrow parent capabilities and scope.
4. Do not let an agent mint or extend its own grants, change tenant context, or impersonate a user.
5. Re-authorize each sensitive operation at execution time; initial task authorization is not a permanent capability.
6. Support cancellation and immediate revocation of delegated grants, including long-running tasks and queued work.
7. Record parent/child agent lineage and the human principal responsible for the task.

## 5. Approval identity and pending actions

### `core/approval_flow.py` and `core/pending_approvals.py`

Strengths:
- Approval requests snapshot the action and arguments.
- Pending records are bound to actor and session.
- Durable storage hashes actor/session identity at rest.
- Pending records have bounded TTL/capacity and atomic one-time claim semantics.
- Audit records are written before an approval request is issued; audit failure removes the pending action.
- Approval consumes the pending action before confirmation, preventing replay.

Limitations:
- The inspected pending record has actor and session bindings, but no explicit tenant ID, agent ID, delegation grant ID, resource ACL revision, or policy revision.
- The coordinator relies on callers to supply trusted actor/session identity. It cannot independently prove that those strings came from the authenticated server session.
- The execution callback receives the action; authorization must still be checked at the final side-effect boundary.

Recommendations:
1. Add tenant ID and authenticated principal type/ID to the immutable approval snapshot.
2. Bind approval to exact action digest, resource ID, tenant, agent/delegation identity, policy version, expiry, and initiating session.
3. Revalidate membership and resource access at approval and again immediately before execution.
4. Do not let a delegated agent approve its own elevated action unless a separately reviewed policy explicitly permits it.
5. Keep one-time consumption, but add revocation and policy-version fencing for pending approvals.
6. Test approval created before membership revocation and submitted after revocation.

## 6. Owner-scoped persistence and tenant isolation

Inspected:
- `core/auth.py`
- `api/oauth_connections.py`
- `core/pending_approvals.py`
- Earlier audits covered conversation and OAuth stores.

Observed:
- Some stores and APIs use explicit owner predicates, which provide a meaningful user-level isolation boundary.
- The inspected authentication model has no tenant-membership relation.
- No conclusion about every cache, queue, websocket, file store, or background worker is possible from this focused sample.

Recommendations:
1. Introduce a canonical `TenantContext` created only by authenticated membership resolution.
2. Require tenant scope in every repository method and enforce it in SQL predicates, not only service-layer filters.
3. Scope caches, vector indexes, file paths, websocket subscriptions, job payloads, event streams, audit records, OAuth connections, and tool grants by tenant.
4. Define explicit sharing for cross-user resources; do not infer access from matching tenant or guessed IDs.
5. Add tenant-aware uniqueness constraints and foreign keys where appropriate.
6. Prevent tenant IDs from being overridden by model-generated arguments or client-supplied metadata.
7. Use tenant-aware cache keys and invalidate them on membership or ACL revision changes.

## 7. Priority remediation

| Priority | Work item | Evidence required |
|---|---|---|
| P0 | Reject duplicate Authorization headers consistently | API-wide duplicate-header regression suite |
| P0 | Establish explicit tenant membership and server-derived tenant context before multi-tenant launch | Cross-tenant authorization and IDOR tests |
| P0 | Introduce bounded agent identities and privilege-attenuating delegation grants | Forged/replayed/escalated delegation tests |
| P0 | Revalidate tenant membership and resource ACL at side-effect boundary | Membership-revocation race tests |
| P1 | Bind approvals to tenant, agent, resource, grant, and policy revision | Approval substitution/replay tests |
| P1 | Inventory every authenticated API and background execution path | Route-to-auth coverage report |
| P1 | Scope caches, jobs, websocket channels, memory and files by tenant | Cross-tenant leakage tests |
| P2 | Add delegation lineage and revocation observability | Audit completeness and incident reconstruction tests |

## 8. Required test plan

1. Duplicate Authorization headers in both orders, including identical values.
2. Missing, malformed, expired, revoked, and disabled-user sessions.
3. User A cannot read, mutate, export, or disconnect User B resources.
4. Tenant A cannot access Tenant B resources even when IDs are known.
5. Tenant membership removal invalidates queued and in-flight privileged operations.
6. Agent cannot expand parent delegated permissions or change tenant.
7. Expired, replayed, wrong-audience, or wrong-task delegation grants fail closed.
8. Approval cannot be transferred between users, sessions, tenants, agents, or resources.
9. Approval created before ACL revision change cannot execute after revocation.
10. Cache, vector memory, file, websocket, and background-job boundaries prevent cross-tenant reads.
11. Logs and traces do not expose bearer tokens, session IDs, or delegated credentials.
12. Authentication or membership service outage fails closed on protected operations.

## Verification status

Targeted source inspection only. The duplicate-header implementation and regression test are submitted in a separate PR; the test has not been run. No complete route inventory, tenant penetration test, delegation runtime test, multi-tenant database migration, cache/job/websocket isolation test, or production identity experiment was performed. Multi-tenant support should not be considered production-ready based on owner-scoped user records alone.
