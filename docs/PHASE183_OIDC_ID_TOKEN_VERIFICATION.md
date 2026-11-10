# Phase 183 — Cryptographic OIDC ID-token verification primitive

## Delivered

- Verify compact JWT signatures against a host-configured JWKS URI fetched through the pinned OAuth HTTPS transport.
- Support RS256, PS256, ES256 and EdDSA/Ed25519 only; reject algorithm confusion and remote key-location headers.
- Require a unique matching `kid` and reject private key material in JWKS responses.
- Validate exact issuer, subject, client audience, authorized party, expiry, issued-at, not-before and nonce claims.
- Bound token size, JWKS key count, RSA key size, token age and clock skew.
- Verify `at_hash` when present.
- Return identity metadata only; never return or log token contents.

## Integration boundary

This is a verification primitive, not yet wired into the authorization-code flow. The next integration phase must add a cryptographically random OIDC nonce to the owner/session-bound durable authorization transaction, include `openid` only for providers configured for OIDC, accept the ID token in a strictly validated token response, verify it before credential persistence, and fail closed if the nonce or identity check fails.

The JWKS URI, issuer, client ID, expected subject and nonce are trusted host configuration or transaction state. They must never be selected by the model or token header. JWKS caching and key-rotation refresh policy are intentionally not introduced in this phase.
