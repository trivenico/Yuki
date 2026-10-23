# Phase 81 — Built-in authentication foundation

## Included

- SQLite-backed built-in accounts for local and hosted application services.
- PBKDF2-HMAC-SHA256 password hashing with per-account random salts (default 600,000 iterations; lower bound 100,000).
- Opaque random session tokens; only SHA-256 token digests are stored.
- Session expiry and explicit revocation; disabled accounts lose access to existing sessions.
- Generic login failures and configurable persistent failed-login lockout.
- Least-privilege user, operator, and admin permission sets.
- One-time first-admin bootstrap and a trusted resolver adapter for workflow approval.
- Tests for account bootstrap, privilege boundaries, session storage/revocation, lockout, disabled users, validation, and approval identity.

## Security and deployment contract

This is a framework-independent authentication service, **not a complete HTTP login server**. It intentionally does not add public self-registration endpoints, browser cookies, CSRF/origin enforcement, password reset, MFA, email verification, or account recovery. A hosted web/API integration must put this service behind HTTPS, extract bearer tokens or session cookies in trusted middleware, set cookies Secure, HttpOnly, and SameSite=Strict where cookies are used, implement CSRF/origin checks for cookie-authenticated mutations, rate-limit at the edge, and never trust client-supplied roles or user IDs. Do not expose create_account as an unrestricted public endpoint without an explicit registration policy.

For local use, keep the SQLite database in Yuki's private data directory and restrict filesystem permissions. Back it up securely. The first-admin bootstrap is permitted only when no account exists; protect the first invocation and credentials as an operator action.

## Workflow approval integration

Use workflow_principal_resolver(auth_service) as the principal_resolver passed to WorkflowApprovalService. The request context must be the session token extracted by trusted transport middleware. Do not pass a username, role, permission list, or actor ID supplied by a request body.

## Current limitations

- No HTTP framework/server exists in the current terminal-first runtime; this phase deliberately avoids introducing an unrequested web framework.
- Password change/reset, MFA, account recovery, session listing, and audit-event integration remain future work.
- The module supports both deployment modes at the service layer, but a hosted web/API must supply the transport security boundary described above.
