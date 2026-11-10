# Phase 167 — OAuth 2.1 PKCE authorization primitives

## Objective

Introduce a small, dependency-free OAuth authorization-code + PKCE S256 foundation for future trusted remote-MCP account linking. The module builds authorization requests and validates callback destinations and state before a future code-exchange service is allowed to run.

## Implemented contract

- Authorization server endpoint must use HTTPS, the default port, contain no user-info or fragment, and match an explicit host allow-list.
- Authorization endpoint query parameters are rejected to prevent pre-seeded OAuth parameter ambiguity.
- Client ID, redirect URI, resource indicator, and scopes are bounded and validated.
- Redirect URI and resource indicator must be HTTPS URLs; callback URI cannot contain a preconfigured query.
- Generates independent cryptographically random state and PKCE verifier for every authorization request.
- Uses only PKCE S256; never supports the weaker `plain` method.
- Emits `response_type=code`, exact redirect URI, normalized scopes, state, challenge, and the OAuth resource indicator.
- Callback validation requires exact scheme/authority/path match, exactly one state and code, constant-time state comparison, no fragment, and no unexpected or duplicate parameters.
- OAuth error callbacks are rejected without exposing provider-supplied descriptions.

## Deliberate non-goals and remaining security work

This is not a complete OAuth client and does not make an Upwork connector usable yet.

- No discovery of Authorization Server Metadata or Protected Resource Metadata.
- No Dynamic Client Registration (DCR) client or persistence of DCR credentials.
- No callback HTTP server, browser session binding, CSRF transaction store, one-time atomic state consumption, or replay prevention. The caller must implement these before production.
- No authorization-code exchange, token validation, refresh rotation, revocation, disconnect, or encrypted credential persistence.
- No issuer/audience validation or token-to-resource binding at the token broker.
- No MCP Streamable HTTP SSE response support.
- No Upwork account link or platform adapter is enabled.
- Deployment must obtain approval for the exact redirect URI from Upwork Support before account linking can be attempted.
- The existing MCP DNS rebinding / connection pinning issue remains a production blocker for arbitrary remote hosts.

## Integration requirements

A future host integration must:
1. Discover and validate metadata from the pinned, reviewed platform authority.
2. Store the authorization transaction server-side, bound to the authenticated Yuki owner and browser session.
3. Atomically consume state once, enforce expiry and issuer checks, and reject replay.
4. Exchange the code using the original PKCE verifier, exact redirect URI, client registration, and resource indicator.
5. Validate token issuer, audience/resource, expiry, scope, and account identity.
6. Encrypt refresh/access tokens using a dedicated credential broker; never expose them to agent context, logs, or ordinary configuration.
7. Serialize refresh-token rotation and revoke/delete credentials on disconnect or account revocation.
8. Keep Upwork discovery owner-directed and neutral: no agent-created job scores, rankings, or autonomous selection. Require exact owner approval for each proposal or message; financial/contract actions remain on the platform.

## Validation

`tests/test_oauth2_pkce.py` covers S256 derivation, random transaction material, URL and scope validation, callback destination, state mismatch, duplicate/unexpected parameters, denial callbacks, and malformed/oversized callbacks. CI is the authoritative test result for the PR; no local execution is claimed.
