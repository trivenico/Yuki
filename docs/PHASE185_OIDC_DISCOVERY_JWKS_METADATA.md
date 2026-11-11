# Phase 185 — OIDC discovery signing-key metadata

## Delivered

- Extend validated authorization-server metadata with an optional trusted JWKS URI.
- Validate the JWKS URI as HTTPS with no userinfo, non-default port, query or fragment.
- Require JWKS URI and signing-algorithm metadata together; partial OIDC signing metadata fails closed.
- Retain only algorithms supported by Yuki's verifier: RS256, PS256, ES256 and EdDSA.
- Reject metadata that advertises no algorithm Yuki can verify.
- Keep non-OIDC OAuth metadata compatible when both OIDC fields are absent.

## Runtime boundary

This phase validates discovery metadata only. It does not fetch JWKS, construct a verifier, enable OIDC for a provider, or connect metadata to the OAuth authorization service. The runtime integration must pass the validated JWKS URI and supported algorithm set from trusted discovery into the verifier, and must use the pinned HTTPS transport.

CI is the validation authority; no local test execution is claimed.
