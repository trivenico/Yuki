# Yuki Web and Runtime Threat Model

**Assessment date:** 2026-11-04  
**Scope:** Authenticated ASGI conversation runtime, static web shell, built-in authentication, local web launcher, provider and tool boundaries.  
**Status:** Initial engineering threat model; **not** an independent security assessment, penetration test, or production approval.

## Security decision

Yuki remains suitable only for continued development and carefully scoped local testing. Do not expose this deployment profile as a public multi-user service until all P0 findings below are closed and independently reviewed.

## Assets

| Asset | Security property |
|---|---|
| User credentials and password verifiers | Confidentiality, integrity |
| Bearer session tokens and provider credentials | Confidentiality, revocability |
| Conversations, private memories, owner preferences | Tenant isolation, confidentiality, integrity |
| Workflow approvals and tool execution authority | Authorization, attribution, integrity |
| Local model/provider configuration | Integrity, confidentiality |
| Workspace databases, embeddings, backups | Confidentiality, recoverability, integrity |
| Imported skills, model output, retrieved memory | Treat as untrusted input |

## Trust boundaries and data flows

1. **Browser to ASGI edge:** HTTP Host, Origin, Fetch Metadata, request body, and bearer token cross an untrusted network boundary. The web wrapper validates an explicit hostname allowlist and checks supplied Origin on state-changing methods. TLS is expected at a trusted deployment edge, not provided by this wrapper.
2. **ASGI API to authentication service:** Login credentials are checked against PBKDF2-HMAC-SHA256 verifiers. Random bearer session tokens are returned to the client; only SHA-256 token digests are stored. Sessions are time-limited and revocable.
3. **Authenticated principal to owner data:** Owner identity is derived from the resolved session, not request-supplied owner fields. Conversation, memory, and profile access use that identity. These controls still require adversarial multi-tenant testing.
4. **Application to tools:** The supplied web launcher uses a deny-all tool policy. This is a deliberate safe default, not evidence that other execution profiles or the local Docker runner are hardened multi-tenant sandboxes.
5. **Application to inference providers:** Providers may be local or externally configured. Prompt content, retrieved memory, provider responses, endpoints, and provider credentials cross separate trust boundaries.
6. **Workspace to filesystem/database:** SQLite stores and model/cache files are local workspace assets. File-system permissions and host compromise remain in scope; encryption at rest and verified recovery are not yet established.

## Existing controls verified in source

- Passwords are validated and hashed with PBKDF2-HMAC-SHA256 (default 600,000 iterations; configured range 100,000–2,000,000).
- Login errors are generic; per-account failed-login counters and temporary lockout exist.
- Session tokens are random; only SHA-256 token hashes are persisted; sessions expire, can be revoked, and are invalidated when an account is disabled.
- First administrator bootstrap is one-time; ordinary account creation receives the least-privileged user role.
- API request bodies are capped at 24,000 bytes; JSON must decode to an object; query parsing has a field-count cap.
- The web shell uses an asset allowlist and emits CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, and no-store for the HTML shell.
- The web launcher binds to loopback by default and requires an explicit opt-in for public binding.
- The web launcher supplies a deny-all tool policy.
- Authenticated memory and profile APIs derive owner scope from the session principal; existing tests cover basic cross-account isolation.
- Provider selection is administrator-controlled and explicit cloud-consent checks exist in the API.
- Host/origin hardening in PR #552 adds exact host allowlisting, rejects missing/duplicate/untrusted Host headers, rejects Sec-Fetch-Site: cross-site on state-changing methods, and validates supplied Origin host and port.

These are source-level observations and test coverage, not proof of production security.

## Threat register

| ID | Threat / abuse case | Current controls | Remaining gap and priority |
|---|---|---|---|
| T1 | Host-header poisoning, password-reset/link poisoning, routing confusion | Exact Host allowlist; duplicate/missing Host rejected; forwarded host ignored | Proxy must preserve/validate Host and be configured explicitly. Verify real proxy behavior. **P0 deployment gate** |
| T2 | Cross-site state-changing browser requests | Origin and Fetch Metadata checks; bearer header auth; no cookie-based session | Requests without Origin are accepted for non-browser clients. Keep bearer-only semantics; if cookies are introduced, require strict CSRF tokens and cookie attributes. Test real browsers. **P0 before browser/public expansion** |
| T3 | Credential interception or session theft on network | Random bearer tokens; hashed server-side token storage; CSP | TLS is external; token is held in sessionStorage and is exposed to successful same-origin script compromise. Require TLS, strict proxy policy, XSS review, CSP regression tests, and session expiry/revocation UX. **P0** |
| T4 | Password spraying / brute force | PBKDF2, generic errors, per-account lockout | No verified IP/network/device rate limiter or global concurrency budget. Per-account lockout can be abused to deny a victim login. Add bounded IP/network throttling, account-aware backoff, and monitoring without trusting spoofable forwarding headers. **P0** |
| T5 | Cross-user conversation or memory access | Session-derived owner identity; owner-scoped stores; basic isolation tests | Require adversarial tenant tests across every endpoint, export/delete, background worker, cache, embedding, and stream/cancel path. **P0** |
| T6 | Unauthorized tool execution / confused deputy | Web launcher denies all tools; API does not accept caller-defined tool schemas | Other profiles and worker execution need per-action authorization, request-scoped identity, fresh approval, audit attribution, and isolation review. Local Docker runner is not a hardened hostile multi-tenant sandbox. **P0** |
| T7 | Prompt injection through user content, memory, imported skills, provider output | Retrieved memory is treated as untrusted reference data; web tools disabled; skill provenance controls are planned | Verify all agent/provider/tool paths preserve policy hierarchy; skill import must be quarantined, reviewed, license-checked, and never auto-executed. **P0** |
| T8 | SSRF, DNS rebinding, malicious redirects via configurable endpoints | Cloud endpoint trust and redirect protection have dedicated tests | Review all URL consumers, DNS resolution/rebinding, private/link-local ranges, redirects, proxy environment variables, and egress policy in deployed topology. **P0** |
| T9 | Request exhaustion / parser abuse | 24 KB body cap; bounded query field count; bounded agent turns/duration | No verified global request concurrency, per-route rate, connection, streaming, or queue limits. Add ingress limits, timeouts, cancellation tests, and resource-exhaustion tests. **P0/P1** |
| T10 | Workspace/database theft or tampering | Local filesystem persistence; SQLite constraints and scoped authorization | No verified encryption at rest, externally managed key lifecycle, authenticated backup, or restore drill. Host-level access remains trusted. **P0** |
| T11 | Supply-chain compromise | Dependency manifest audit workflow | Independent review, pinned/reproducible builds, provenance/signing, secret scanning, and imported skill/model provenance still need a release gate. **P1** |
| T12 | Operational failure hides compromise or data loss | Some CI, health endpoint, and test coverage | Structured redacted logs, durable security audit, alerting, incident response, recovery objectives, and on-call/runbooks are incomplete. **P1** |

## Required verification work

### P0 — before private beta or public exposure

1. Add network-aware login and API rate limiting with bounded storage, safe proxy trust, and tests for lockout abuse.
2. Perform adversarial tenant-isolation tests for all routes, streaming, cancellation, memory, profile, provider changes, workflow approvals, caches, and background workers.
3. Verify deployment TLS, proxy header behavior, trusted hosts, Origin handling, ingress body/concurrency/time limits, network egress, and secret provisioning.
4. Complete a threat-led review of every tool execution path and approval-to-execution identity propagation.
5. Audit URL fetch/provider endpoint paths for SSRF, DNS rebinding, redirect, and private-network access.
6. Implement encrypted, authenticated, database-consistent backups with external key management and tested restore/tamper/wrong-key behavior.
7. Define data export/deletion/retention semantics, including backups, caches, and derived embeddings.
8. Commission an independent security review and close or explicitly accept all critical/high findings.

### P1 — before production candidate

- Structured redacted logs, correlation IDs, actor-attributed security audit, metrics, alerts, health/readiness, and incident runbooks.
- Load/soak tests and bounded resource policies for HTTP, streaming, model calls, workers, SQLite/PostgreSQL, and queues.
- Reproducible dependency installation, supply-chain review, secret scanning, and build provenance.
- Browser XSS/accessibility/session-expiry tests on supported browsers and devices.
- Clean install, upgrade, rollback, backup restore, and uninstall drills on supported operating systems.

## Abuse-case test matrix

| Test | Expected result |
|---|---|
| Unknown, duplicate, missing, malformed Host | Reject before API dispatch |
| Spoofed X-Forwarded-Host | Does not change the accepted host |
| Cross-site Fetch Metadata on POST/PATCH/PUT/DELETE | Reject before API dispatch |
| Origin host or port mismatch | Reject before API dispatch |
| Repeated invalid passwords from one source | Bounded rate response; no unbounded CPU or storage growth |
| One user guesses another user's conversation/memory ID | No data or existence leakage |
| Revoked/expired/disabled user's session | Reject |
| Untrusted memory/skill contains tool instructions | Must not elevate permissions or execute |
| Provider endpoint redirects to loopback/private metadata service | Reject unless explicitly trusted and policy permits |
| Backup tampered, wrong key, partial/corrupt | Restore fails closed without overwriting live data |

## Limitations and review record

- This is a first-party engineering assessment based on the current repository source and tests.
- No live production environment, proxy, DNS, TLS certificate, cloud account, or deployed database was penetration-tested.
- No claim is made that the threat register is exhaustive.
- A green CI run verifies configured automated checks only; it does not close the open risks above.
- Reassess this document when authentication, web deployment, provider networking, tools, storage, or multi-user execution changes.