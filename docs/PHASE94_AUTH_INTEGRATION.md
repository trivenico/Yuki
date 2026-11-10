# Phase 94 — built-in authentication integration boundary

## What is added

- `core/auth.py` resolves a session into a workflow approval principal using the opaque database session ID. Approval records therefore bind to the server-side session identifier rather than copying the raw bearer token into the approval boundary.
- `core/auth_http.py` provides framework-neutral ASGI middleware, principal extraction, session cookie issuance and logout-cookie clearing.
- Hosted HTTP requests can resolve the current user from the `yuki_session` cookie. Unsafe authenticated requests require a matching `yuki_csrf` cookie and `X-CSRF-Token` header.
- Session cookies are HttpOnly, Secure by default, SameSite=Strict, Path=/, and bounded to the session expiry. CSRF cookies are not HttpOnly because the frontend must echo the value in the request header. Logout clears both cookies.
- Adds integration tests for principal injection, revocation, CSRF rejection/acceptance, cookie attributes, and workflow approval permissions.

## Integration requirements for a hosted application

Yuki currently has no single production HTTP application/server entry point in `requirements.txt` or the repository entry-point inventory. This phase provides a reusable ASGI boundary, not a deployed login UI or complete API server.

The future host must:
1. Wrap the ASGI app with `YukiAuthMiddleware(app, auth_service, secure_cookies=True)`.
2. Implement login with `auth_service.authenticate(username, password)`, return the issued cookie headers, and never put the token in JSON, URLs, logs, or local storage.
3. Require `principal_from_scope(scope)` on every protected route; middleware intentionally permits anonymous requests to reach public/login routes.
4. Use `issue_session_cookie_headers` on login and `clear_session_cookie_headers` on logout; revoke the session server-side before clearing cookies.
5. Use HTTPS and trusted proxy configuration in hosted deployments. Do not set `secure_cookies=False` for a public deployment.
6. Configure account bootstrap out of band, do not ship default credentials, and restrict initial admin creation to an operator-controlled setup step.
7. Apply resource-level authorization to workspace, workflow, and tenant resources; authentication alone is not authorization.

## Local terminal limitation

The current terminal application `yuki.py` is still a local interactive CLI and does not yet require a login before commands execute. The ASGI middleware is for a future/current ASGI host and does not magically secure the CLI. A separate local-login integration and a real hosted server entry point remain follow-up work.

## Security limitations and operational requirements

- SQLite database files and their containing directories must be private to the Yuki OS account; backups must be encrypted and access controlled. SQLite WAL files are also sensitive.
- Deploy behind TLS, keep session tokens out of logs, and set correct trusted-proxy/host/origin configuration.
- Cookie-based sessions need CSRF validation for state-changing authenticated routes. The middleware checks a double-submit token; the host must not disable this check or bypass the middleware on mutation routes.
- Add CSP, origin/host validation, security headers, account recovery policy, password-change UI, admin account lifecycle UI, and independent security review before public launch.
- Login lockout is a baseline control, not a substitute for network/IP rate limiting and abuse monitoring.
- The authentication service does not protect a CLI process or arbitrary plugin code unless those entry points explicitly enforce the principal.

## Validation

CI runs the existing authentication and workflow approval suites plus ASGI middleware tests on Python 3.11 for Ubuntu, macOS, and Windows. Merge only when exact-head checks pass and the host integration caveats above are accepted.
