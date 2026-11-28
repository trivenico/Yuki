# OAuth Connection API Duplicate Authorization Header Audit

## Finding

**Severity: Low — ambiguous bearer authentication headers are accepted by the connection API**

The authenticated OAuth connection API silently collapses duplicate Authorization headers into a dictionary and uses the last value encountered. The OAuth authorization API rejects requests unless exactly one Authorization header is present.

This creates inconsistent authentication parsing across two adjacent authenticated OAuth API boundaries. Intermediaries and application layers may normalize duplicate headers differently, making requests with ambiguous credentials difficult to reason about and audit.

## Source evidence

- `api/oauth_connections.py`, `AuthenticatedOAuthConnectionAPI._user()`:
  - Builds a dictionary comprehension from all byte-valued request headers.
  - Duplicate header names overwrite earlier values.
  - Reads the resulting `authorization` value and parses it as a bearer token.
  - Does not count or reject duplicate Authorization headers.
- `api/oauth_authorization.py`, `AuthenticatedOAuthAuthorizationAPI._session_token()`:
  - Collects all Authorization header values.
  - Requires exactly one value and returns no session token when zero or multiple values are present.

## Impact and boundaries

- A request containing multiple Authorization headers is accepted by the connection API if the final value selected by the ASGI header iteration order is a valid bearer session.
- Different proxies, gateways, middleware, and application components can disagree about which duplicate value is authoritative or whether duplicates should be rejected.
- This is an authentication parsing ambiguity and policy inconsistency, not a demonstrated session-validation bypass. The selected token is still passed to `auth.resolve_session()`.
- No evidence was found that an untrusted caller can alter header ordering after the request reaches the ASGI application; actual exploitability depends on the deployment's upstream HTTP stack.

## Recommended remediation

1. Reuse one shared bearer-header parser for both OAuth APIs.
2. Reject requests unless exactly one Authorization header is present.
3. Reject malformed bearer syntax and empty/oversized tokens consistently.
4. Add tests for duplicate headers in both orders, mixed-case header names, malformed schemes, and duplicate values that differ.
5. Ensure upstream proxy/server configuration rejects or consistently normalizes duplicate Authorization headers.

## Review limitations

This is a source-level inconsistency. No live proxy behavior or runtime request was tested. No authentication bypass was demonstrated.
