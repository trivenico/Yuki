# Yuki Engineering Audit 060 — API Authentication, Authorization, and Tenant Isolation

**Review date:** 2026-11-23  
**Repository:** `trivenico/Yuki`  
**Review type:** Targeted source inspection and architecture recommendations  
**Status:** Findings are not a production security certification.

> Scope: This audit inspects selected API and security files on the repository's `main` branch. It does not claim every route, middleware composition, deployment setting, or persistence adapter was reviewed. No tests, penetration tests, or production traffic checks were executed.

## Executive assessment

Yuki has useful security foundations in its current API surface:

- OAuth authorization endpoints resolve an authenticated session before starting or completing a provider flow.
- OAuth connection listing and disconnection are scoped to the resolved user ID.
- Provider services are resolved from host configuration rather than accepting arbitrary provider URLs from the request.
- The web shell uses an explicit static asset allowlist and sets several browser security headers.
- Tool authorization has a default-deny tool-name allowlist.
- Audit logging stores bounded metadata and redacts common credential patterns.

However, the inspected code does not establish a complete multi-tenant authorization system. The OAuth endpoints use a user owner ID, but no organization/tenant membership or role boundary is visible in these inspected handlers. The tool policy authorizes names only; it does not authorize a principal against an action and resource. Route-by-route authentication coverage, CSRF posture, rate limiting, session lifecycle, and tenant-aware storage enforcement remain to be verified.

## Inspected files and concrete observations

### `api/web.py`

- Serves only a fixed mapping of static assets.
- Applies `nosniff`, `no-referrer`, `DENY` framing, and a restrictive same-origin content security policy.
- Health endpoint returns a static service status.
- API requests are delegated to the wrapped application.

**Follow-up:** Confirm middleware order and that every delegated API route is protected as intended. Health should not expose deployment internals; add readiness separately if dependency health is needed.

### `api/oauth_authorization.py`

- Requires a bearer session token and resolves it through the configured auth service.
- Uses a host-provided provider-service resolver; request input cannot select an arbitrary provider URL.
- Binds OAuth operations to `user.user_id`.
- Validates callback payload shape and uses generic errors to avoid echoing authorization codes.
- Attempts compensation if connection registration fails.

**Follow-up:** Bind OAuth state/PKCE/nonce to the initiating user session, enforce one-time callback consumption and strict redirect URI allowlists, and test replay, account mix-up, and session substitution. Confirm token parsing, revocation, expiry, and constant-time comparisons in the underlying auth/provider services.

### `api/oauth_connections.py`

- Resolves a bearer session before listing or disconnecting connections.
- Uses owner-scoped registry calls for list/get/remove.
- Returns connection metadata rather than credentials.
- Uses 404 for a connection not owned/found, reducing direct ownership disclosure.

**Follow-up:** Reject ambiguous duplicate Authorization headers consistently; this handler currently constructs a dictionary from headers, so duplicate keys collapse. Add rate limits and audit events for connect/disconnect operations. Ensure remote revocation failures are surfaced accurately and do not silently imply credentials are revoked.

### `yuki_security.py`

- `SecurityPolicy` defaults to a small allowlist and rejects unknown tool names.
- Audit records are bounded and metadata-only by contract.
- Common token patterns are redacted before audit storage.

**Follow-up:** A tool-name allowlist is not a complete authorization decision. Introduce principal, tenant, agent, capability, action, resource, policy version, and expiry into the decision. Redaction patterns are defense-in-depth, not a substitute for never collecting secrets.

## Target request authorization contract

Every authenticated request should resolve a trusted context from verified credentials and server-side membership data:

```text
Request
  -> transport / size / content-type validation
  -> credential extraction (reject duplicates / ambiguity)
  -> token verification and session status
  -> principal resolution
  -> tenant membership resolution
  -> route permission check
  -> resource ownership / sharing check
  -> quota and rate-limit check
  -> application service
  -> output field filtering
  -> metadata audit event
```

A suggested immutable context:

```python
from dataclasses import dataclass
from typing import FrozenSet

@dataclass(frozen=True)
class RequestPrincipal:
    user_id: str
    tenant_id: str
    session_id: str
    roles: FrozenSet[str]
    authn_method: str
    authz_policy_version: str
```

This is a proposed interface, not an existing verified class. Construct it only after verifying credentials and resolving active tenant membership server-side. Never trust a tenant ID supplied in a request body, query string, URL path, or unsigned token claim without checking current membership.

## Required security invariants

1. **Authentication is not authorization.** A valid session does not imply permission to access every route, tenant, or resource.
2. **Tenant context is server-derived.** Resolve tenant membership from trusted storage; do not accept arbitrary client-selected tenant IDs.
3. **Authorize every object access.** Read, update, delete, export, and action endpoints must check tenant and resource ownership or explicit sharing.
4. **Default deny.** Unknown routes, roles, capabilities, and resource scopes are denied.
5. **No IDOR.** Changing a resource identifier must never expose another user's or tenant's object.
6. **Agent identity is separate.** An agent acts under a bounded delegated grant, not as an unrestricted user session.
7. **Privilege attenuation.** Delegated scopes cannot exceed the delegator's effective permissions.
8. **Approval is action-bound.** Human approval must bind to the exact tenant, principal, action, resource, and immutable payload digest.
9. **Session lifecycle is explicit.** Expiry, logout, revocation, credential rotation, and account disablement must invalidate access promptly.
10. **Secrets stay out of telemetry.** Never log bearer tokens, cookies, OAuth codes, provider credentials, raw authorization headers, or sensitive request bodies.
11. **Rate limits are layered.** Apply limits by IP/network where appropriate, principal, tenant, route, provider, and expensive operation.
12. **Fail closed.** If identity, membership, policy, approval, or audit dependencies fail, protected operations do not proceed.

## Tenant-aware data model

Introduce or verify a central model with constraints equivalent to:

| Entity | Key fields / constraints |
|---|---|
| `tenants` | tenant ID, status, plan/limits, created timestamp |
| `tenant_memberships` | tenant ID, user ID, role, status; unique active membership policy |
| `users` | stable user ID, account status |
| `sessions` | session ID/hash, user ID, issued/expiry/revoked timestamps |
| `api_credentials` | credential ID/hash, owner, scopes, expiry, last-used |
| `agent_identities` | agent ID, tenant, owner, lifecycle, registered capabilities |
| `resource_acl` | tenant, resource type/ID, subject, permission |
| `authorization_decisions` | decision ID, principal, tenant, action, resource, policy version, outcome |
| `security_audit_events` | timestamp, actor, tenant, action, resource reference, outcome, correlation ID |

Enforce tenant scoping in repository/data-access methods and database constraints. For a relational database, consider row-level security as an additional guard, not a replacement for application authorization. Ensure background jobs, exports, websocket events, caches, and agent memory use the same tenant boundary.

## Endpoint and middleware requirements

### Credential handling

- Accept one unambiguous credential source per route; reject duplicate or conflicting Authorization headers.
- Prefer short-lived access sessions and revocable refresh/session records.
- If JWTs are used, pin allowed algorithms and validate issuer, audience, expiry, not-before, token type, key ID, and signature. Do not accept algorithm negotiation from untrusted token headers.
- Store opaque session tokens hashed at rest where possible.
- Redact credentials before error reporting and tracing.

### Browser session and CSRF

- If browser authentication uses cookies, use `Secure`, `HttpOnly`, and an appropriate `SameSite` policy.
- Protect state-changing cookie-authenticated routes with CSRF tokens and Origin/Referer validation.
- If using bearer tokens in browser storage, explicitly assess XSS exposure and token theft; CSP is helpful but not sufficient.
- OAuth callback state, PKCE verifier, redirect URI, provider, user, and initiating session must be bound together and consumed once.

### CORS, headers, and transport

- Keep CORS disabled unless cross-origin clients are required; otherwise use an exact allowlist and credentials policy.
- Require HTTPS at the edge and configure trusted proxy handling narrowly.
- Add HSTS at the TLS-terminating edge where appropriate.
- Keep CSP restrictive and test actual application assets against it.
- Apply request body limits, timeouts, content-type validation, and safe JSON parsing to every API route.

### Authorization and resource access

- Use a central policy interface such as `authorize(principal, action, resource)`.
- Check ownership/sharing at the service or repository boundary, not only in route code.
- Make tenant ID part of every tenant-owned query predicate and unique key.
- Avoid cache keys that omit tenant and authorization context.
- Filter response fields to the minimum necessary for the caller.
- Return consistent errors without leaking whether another tenant's resource exists.

### Operational controls

- Per-user and per-tenant quotas, concurrency caps, and route-specific rate limits.
- Security event alerts for repeated failures, token replay, unusual export volume, privilege changes, and cross-tenant denial attempts.
- Key rotation, emergency session revocation, tenant suspension, and credential revocation runbooks.
- Audit logs should be append-only or tamper-evident in production storage, access-controlled, and retention-managed.
- Do not make a protected side effect contingent on best-effort audit logging if policy requires an audit record; fail closed or use a durable transactional outbox.

## High-priority test plan

### Authentication

- Missing, malformed, expired, revoked, wrong-audience, wrong-issuer, wrong-signature, and wrong-token-type credentials.
- Duplicate Authorization headers and conflicting credential sources.
- Logout and credential rotation invalidate old sessions.
- Disabled user and suspended tenant are denied.
- OAuth state replay, PKCE mismatch, redirect mismatch, account mix-up, and callback from another session are denied.

### Authorization / IDOR

- User A cannot read, update, delete, or export User B's resource by changing IDs.
- Tenant A member cannot access Tenant B resources, even with a valid session.
- A user with multiple tenants can only access the currently resolved active membership.
- Read-only role cannot perform write/admin operations.
- Delegated agent cannot access scopes absent from its grant.
- Approval cannot be reused after payload, resource, tenant, or task changes.

### API robustness

- Oversized, deeply nested, malformed, and unexpected-content-type requests are rejected safely.
- Rate limits apply across multiple application workers.
- Cache and websocket fan-out never cross tenant boundaries.
- Error responses and traces contain no credentials or sensitive request bodies.
- Policy-store or membership-store outage blocks protected operations.

## Prioritized remediation

| Priority | Work item | Completion evidence |
|---|---|---|
| P0 | Inventory every API route and middleware wrapper | Route matrix with authn/authz requirements |
| P0 | Introduce server-derived tenant context | Tests for membership and cross-tenant denial |
| P0 | Central resource authorization | IDOR regression suite across read/write/delete/export |
| P0 | Harden session and OAuth lifecycle | Replay, expiry, revocation, PKCE and callback binding tests |
| P1 | Layered rate limits and request limits | Multi-worker load and abuse tests |
| P1 | Tenant-aware persistence and cache keys | Isolation tests across DB, cache, jobs, websockets |
| P1 | Agent identity and attenuated delegation | Delegation escalation tests |
| P2 | Security monitoring and incident runbooks | Alert simulations and documented recovery exercises |

## Verification status

The inspected files provide evidence of selected controls, not complete API coverage. The following remain unverified:

- All API route authentication and authorization coverage.
- Auth service token verification, session storage, expiry, and revocation semantics.
- Tenant membership and organization model across the application.
- Database-level tenant isolation and cache/job/websocket scoping.
- CORS, TLS, proxy, cookie, and CSRF deployment configuration.
- Rate limiting and abuse controls.
- Full OAuth state/PKCE/replay behavior in the provider service.
- Penetration testing and automated IDOR regression coverage.

Do not describe Yuki as multi-tenant secure until these invariants are implemented and tested across every API, background worker, integration, and data-access path.
