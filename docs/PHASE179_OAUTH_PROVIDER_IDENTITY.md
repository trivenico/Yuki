# Phase 179 — OAuth provider account identity verification

## Delivered

- Add a host-configured provider identity verifier using a trusted userinfo endpoint.
- Send the access token only as a bearer credential to the pinned OAuth HTTPS transport.
- Require the returned sub to match the expected provider account subject configured by the host.
- If the provider returns iss, require it to match the configured issuer.
- Optionally enforce the expected audience when the provider's userinfo response supplies it.
- Require the authorization exchange coordinator to verify provider identity before storing any credential.
- Fail closed on lookup errors, malformed responses, subject mismatches, issuer mismatches, and configured audience mismatches.
- Never decode opaque access tokens or trust unverified JWT claims.

## Integration contract

The host must configure the exact userinfo endpoint in the transport's HTTPS host allow-list and supply the expected provider subject from a trusted account-linking flow. The verifier does not discover endpoints or accept a subject from model-generated input.

The current verifier uses the provider's authenticated userinfo response as proof of account identity. Provider-specific OIDC ID-token signature, issuer, audience, nonce verification and authorization-response iss validation remain separate hardening work.
