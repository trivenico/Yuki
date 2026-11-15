# Phase 193 — Google Gmail OAuth provider composition

## Scope

This phase composes the existing durable OAuth transaction store, encrypted
credential vault, pinned OAuth HTTP transport, PKCE authorization service,
OIDC verifier, provider identity verifier, and prepared-provider registry
contract for Google Gmail.

## Security properties

- Google authorization and token endpoints are fixed in trusted code.
- The redirect URI must be HTTPS and use the exact
  `/oauth/callback/google` path.
- The pinned transport must allow the exact Google authorization, token,
  JWKS, and userinfo hosts; DNS pinning and the shared network firewall remain
  enforced by `PinnedOAuthHTTPTransport`.
- Authorization uses S256 PKCE, a transaction-bound OIDC nonce, offline access,
  and explicit user consent for a refresh-token grant.
- No RFC 8707 `resource` parameter is sent to Google's authorization endpoint.
- Identity is cryptographically verified from Google's signed ID token using
  the configured issuer, client ID, JWKS, expiry, nonce, and signature checks.
- Access-token userinfo is independently checked against the signed ID-token
  subject. Email and profile names are not used as account identifiers.
- The host supplies an account resolver that derives an opaque owner-scoped
  account ID from the verified OIDC subject.
- The requested Gmail grant is limited to `gmail.send` and `gmail.readonly`;
  it does not request full mailbox access or `gmail.modify`.
- The resulting provider is returned as the existing immutable
  `PreparedOAuthProvider` contract and can be published through
  `PreparedOAuthProviderRegistry`, which is already wired into the authenticated
  OAuth API by the runtime composition root.

## Deliberate non-goals

This composition does not itself register a Gmail tool, create a production
Google Cloud OAuth client, store client secrets, configure a public callback
domain, grant consent, or expose sending to users. Deployment must provision
the OAuth client and callback URI, configure all pinned hosts and trusted
identity/account mapping, and supply the existing durable transaction store
and encrypted credential vault. Gmail tool registration, approval UI,
execution/reconciliation wiring, and end-to-end provider tests remain separate
integration milestones.

## Validation

Unit tests cover Google authorization URL construction, PKCE/offline parameters,
resource omission, least-scope selection, callback validation, pinned host
requirements, dynamic OIDC subject configuration, and compatibility with the
prepared OAuth provider registry contract.
