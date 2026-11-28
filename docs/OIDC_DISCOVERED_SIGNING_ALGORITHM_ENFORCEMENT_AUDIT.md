# OIDC discovery signing-algorithm policy is not enforced by the verifier

## Finding

**Severity: Medium — discovered ID-token signing algorithm policy is not bound to runtime verification**

Authorization-server discovery validates and retains the provider's `id_token_signing_alg_values_supported` metadata, but the prepared authorization service does not enforce that set when verifying ID tokens. The verifier accepts every algorithm in its own static allow-list, regardless of the algorithms advertised by the discovered issuer.

## Evidence

- `core/mcp_oauth_metadata.py::validate_authorization_server_metadata()` validates the discovery field and stores the supported intersection in `AuthorizationServerMetadata.id_token_signing_alg_values_supported`.
- `core/mcp_oauth_provider_bootstrap.py::MCPProviderOAuthBootstrap.prepare()` receives the validated metadata, but constructs `OAuthAuthorizationService` using an externally resolved `oidc_verifier_for_provider` result. It does not pass the discovered signing-algorithm set or verify that the supplied verifier is constrained to it.
- `core/oidc_id_token.py::OIDCIdentityTokenVerifier.verify()` accepts the static set `RS256`, `PS256`, `ES256`, and `EdDSA`. It does not consult discovery metadata.
- `OIDCIdentityTokenVerifier._public_key()` checks that a JWK's optional `alg` matches the token algorithm when present, but this only binds the token to the JWK; it does not enforce the issuer's discovered algorithm policy.
- `docs/PHASE185_OIDC_DISCOVERY_JWKS_METADATA.md` explicitly says validated JWKS URI and supported algorithm metadata must be connected to the verifier by runtime integration. The current bootstrap does not complete that binding.

## Example failure mode

1. Trusted discovery metadata for an issuer advertises only `RS256`.
2. The host-provided OIDC verifier is configured with the issuer's JWKS URI but retains the verifier's broader static algorithm allow-list.
3. The issuer's JWKS contains a valid ES256 signing key and an ID token is signed with ES256.
4. The verifier accepts ES256 if the signature and claims validate, despite ES256 not being in the discovered issuer policy.

The same gap applies to any mismatch between the discovered algorithm set and the verifier's static allow-list. This is not an attacker-controlled JWKS URL or an algorithm-confusion bypass: issuer metadata, verifier resolver and verifier configuration are trusted host-side inputs. It is a failure to enforce a validated security policy at the final verification boundary.

## Impact

The verifier can accept an ID-token signing algorithm that the issuer did not advertise as supported. This weakens algorithm-policy enforcement and makes the effective trust configuration broader than the discovered provider metadata indicates. Impact depends on issuer behavior, JWKS contents, and host verifier configuration; no direct credential-forgery path is established by this source review.

## Recommended remediation

1. Carry the validated `id_token_signing_alg_values_supported` set into the OIDC verifier as an explicit immutable allowed-algorithm policy.
2. Require each token's `alg` to be in both the verifier's cryptographic implementation allow-list and the discovered issuer's advertised set.
3. Bind the verifier's issuer, client ID and JWKS URI to the same validated discovery snapshot used to prepare the OAuth authorization service; reject mismatched resolver output during bootstrap.
4. Add an integration regression test where metadata advertises only RS256 but a correctly signed ES256 ID token is presented; assert rejection before credential persistence.
5. Add positive tests for each advertised supported algorithm and a bootstrap test proving the exact discovered policy reaches the verifier.

## Scope and confidence

The source-level policy propagation gap is confirmed. The example requires a valid issuer signing key/token using an algorithm outside the issuer's advertised set; no unauthenticated remote control of trusted host configuration was identified. No runtime tests were executed as part of this review.
