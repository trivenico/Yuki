# Phase 184 — OIDC authorization flow integration

## Delivered

- OIDC-enabled authorization requests add the openid scope and a cryptographically random nonce.
- The nonce is stored encrypted in the existing durable OAuth transaction, alongside the encrypted PKCE verifier and owner/session-bound state.
- Existing transaction databases are migrated by adding a nullable encrypted nonce column; non-OIDC transactions remain compatible.
- Token responses may contain a strictly bounded compact id_token field. Its value is excluded from grant representations.
- The authorization service requires and verifies the ID token against the transaction nonce before calling the credential vault.
- OIDC-enabled flows fail closed if the ID token or nonce is missing, or if verification fails.
- Non-OIDC flows reject unsolicited ID tokens rather than silently accepting an unverified identity assertion.
- Userinfo/provider identity verification remains in place as an additional account-binding check.

## Trust boundary

The OIDC verifier is injected by trusted host configuration. Its issuer, JWKS URI, client ID and expected subject must come from validated provider configuration and the owner-selected account binding—not model output, callback parameters or JWT headers. The verifier uses the pinned HTTPS transport for JWKS retrieval.

## Not included

- Provider discovery/runtime wiring for jwks_uri and supported signing algorithms.
- A callback HTTP route or user-facing account-linking interface.
- Provider-specific production configuration or a live account connection.
- JWKS caching and key-rotation refresh policy.

CI is the validation authority; no local test execution is claimed.
