# Audit 078 — Authentication, Session Security, and Zero-Trust Runtime Identity

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Scope:** built-in authentication, password verification, session lifecycle, API principal extraction, approval identity, and agent/service identity boundaries.

## Executive summary

The built-in auth service has a solid baseline: PBKDF2-HMAC-SHA256 password hashes with per-account salts, random opaque bearer sessions with only SHA-256 token digests persisted, bounded session lifetimes, server-side revocation, disabled-account checks, generic credential errors, account lockout, and least-privileged signup defaults. Approval records bind actor and session and are consumed atomically.

The inspected implementation is still a user-session system, not a complete zero-trust identity plane for autonomous agents and internal services. MFA, recovery, service principals, workload identity, short-lived scoped delegation, credential rotation, and continuous authorization at every side-effect boundary were not demonstrated in these paths.

## Confirmed finding

### P1 — Disabled and locked accounts had distinguishable login work

Before this fix, unknown usernames incurred a dummy PBKDF2 derivation, but disabled accounts returned immediately and currently locked accounts also returned before password derivation. Although the API returned the same generic invalid-credentials response, response latency exposed a different authentication path for known disabled/locked accounts versus unknown usernames.

**Fix submitted:** perform a PBKDF2 derivation using the stored per-account salt and iteration count for disabled and locked accounts before returning the same generic failure. This equalizes the expensive password-hash operation across these account states; it does not guarantee perfectly constant wall-clock timing across a database-backed service.

**Regression tests added:** assert that disabled and locked account failures each perform the password derivation. Tests have not been executed.

## Existing positive controls

- PBKDF2-HMAC-SHA256 with per-account random salt and configurable bounded iteration count.
- Opaque random bearer tokens; only SHA-256 token hashes are persisted.
- Session expiry is checked server-side on every resolution.
- Logout revokes a session; account disable revokes all active sessions.
- Password failures use a generic error and unknown usernames receive dummy password-hash work.
- Account lockout state is persisted.
- New accounts receive the least-privileged user role.
- Initial admin bootstrap is one-time and transactionally guarded.
- Approval identity is resolved from the authenticated session; pending approvals bind actor and session and are one-time consumed.
- API response paths use no-store headers in the reviewed tests.

## Additional findings and architecture gaps

### P1 — Duplicate Authorization header ambiguity remains on main

The reviewed `api/conversations.py` principal extractor converts ASGI headers to a dictionary, which silently collapses duplicate Authorization fields. A previous focused fix was submitted in PR #1131, but it remains a separate, unmerged proposal as of this audit. Do not assume the main branch includes it. All authentication middleware and wrappers must consistently reject zero or multiple Authorization values and validate the bearer grammar.

### P1 — No first-class workload/service identity in reviewed auth model

The built-in auth schema contains users and sessions, but no separate service-principal or workload identity model with audience, scopes, expiry, rotation, and revocation. Background workers and agent runtimes should not borrow an administrator's interactive session as their machine identity.

### P1 — Agent delegation needs explicit, attenuating grants

Approval state binds actor and session, but the inspected model does not itself represent a signed delegation grant with issuer, subject agent, audience, allowed tools/actions, resource scope, expiry, policy version, and revocation. Introduce delegation as an explicit, privilege-attenuating capability and revalidate it at the side-effect boundary.

### P1 — MFA and account recovery were not demonstrated

The inspected auth service has password login and server-side sessions, but no MFA enrollment/challenge/recovery flow, recovery-token lifecycle, or privileged step-up authentication. Sensitive actions such as changing credentials, adding integrations, changing global providers, or granting agent capabilities should require step-up authentication and auditable confirmation.

### P2 — Session lifecycle needs operational controls

The schema supports expiry and revocation, but the inspected service does not demonstrate session inventory, per-device/session labels, bulk revoke, idle timeout, refresh-token rotation, or automated expired-session cleanup. These should be designed without exposing bearer tokens.

### P2 — Role authorization is coarse

Built-in role permissions are static role-to-permission mappings. They do not express tenant membership, resource ownership, per-agent scope, or dynamic policy versions. Keep authorization decisions resource-scoped and deny by default.

### P2 — TLS and cookie controls are deployment/API integration responsibilities

The auth module explicitly is not an HTTP server or TLS substitute. Hosted deployments must enforce TLS, trusted proxy boundaries, secure cookie attributes if cookies are introduced, origin/CSRF controls for cookie-authenticated flows, and redaction of Authorization headers in logs and traces.

## Recommended implementation sequence

1. Merge and validate the outstanding duplicate-Authorization fix (PR #1131) together with this audit's branch before relying on API header parsing.
2. Add dedicated workload identities and short-lived audience-bound service credentials; prohibit sharing human bearer tokens with background agents.
3. Define signed, attenuating agent delegation grants and validate them at every tool, filesystem, integration, and external side-effect boundary.
4. Add MFA and step-up authentication for sensitive account and privilege changes.
5. Add session inventory, per-session revocation, and safe operational expiry cleanup.
6. Build a centralized authorization decision interface that combines principal, tenant, resource, action, agent delegation, and policy revision.
7. Add adversarial integration tests for duplicate headers, revoked/expired tokens, token replay, cross-user and cross-tenant access, privilege changes during long-running jobs, and delegation expiry/revocation.

## Validation limits

Source inspection only. The new regression tests were added but not executed. No timing benchmark, concurrent brute-force/load test, live ASGI/proxy test, MFA provider integration, tenant isolation test, or distributed workload identity deployment was exercised. This audit does not claim complete constant-time behavior or production readiness.
