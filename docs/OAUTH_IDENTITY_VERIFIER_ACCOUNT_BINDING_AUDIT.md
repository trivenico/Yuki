# OAuth Provider Identity Verifier Account-Binding Audit

## Scope

Source review at PR #1346 head `e9a538868309d14586b5347d76d380f5970118d1`. Reviewed provider bootstrap, identity verifier, authorization service, and their tests. This is distinct from the provider-configuration credential-generation audit: this finding concerns the identity-verifier resolver's account granularity during new authorization.

## Finding: identity verifier is resolved by provider ID, not configured account ID

**Severity: Medium — cross-account credential misbinding under multi-account provider configuration.**

`OAuthProviderBootstrapConfig` contains both `provider_id` and `account_id`. The authorization service persists a successfully verified grant under the configured account ID.

However, `MCPProviderOAuthBootstrap.prepare()` obtains the identity verifier using:

```python
identity = self._identity_for(config.provider_id)
```

The resolver contract therefore receives no account ID. The same is true of the optional OIDC verifier resolver.

`OAuthProviderIdentityVerifier` is explicitly configured with one `expected_subject`, one expected issuer, and an optional audience. Its verification checks the returned userinfo `sub` against that one subject. The verifier has no awareness of the `account_id` to which the authorization service will persist the grant.

As a result, if a host prepares two account configurations for the same provider ID, both authorization services can be given the same verifier. If that verifier expects account A's external subject, a token for account A can pass identity verification in the authorization service configured to persist under internal account B. The grant is then stored under B's vault key. Conversely, a verifier configured for B can reject a legitimate authorization for A. The exact outcome depends on the host resolver's implementation, but the bootstrap API does not provide the account identity needed to select a distinct verifier.

The shipped bootstrap tests use a resolver accepting only one positional provider argument and do not exercise two account configurations for the same provider. Identity verifier tests cover subject mismatch in isolation, not resolver selection or account-to-subject binding.

## Evidence

- `core/mcp_oauth_provider_bootstrap.py`: `OAuthProviderBootstrapConfig` includes `provider_id` and `account_id`; `prepare()` calls `self._identity_for(config.provider_id)` and passes the result into the authorization service.
- `core/oauth_provider_identity.py`: verifier stores one configured `expected_subject` and validates userinfo `sub` against it.
- `core/oauth_authorization_service.py`: after identity verification, `complete()` stores the grant using its configured `self._account_id`.
- `tests/test_mcp_oauth_provider_bootstrap.py`: test resolver is `lambda _: Verifier()`; no multi-account resolver test.
- `tests/test_oauth_provider_identity.py`: validates a verifier's subject comparison but not account-specific resolver binding.

## Recommended remediation

1. Resolve identity verification using a host-controlled composite identity, at minimum `(provider_id, account_id)`, or a typed immutable account configuration object containing both.
2. Apply the same account-specific binding to the optional OIDC verifier resolver where its expected subject/audience/nonce policy is account-specific.
3. Make the verifier's expected subject binding inspectable as trusted configuration metadata, or have the resolver return a typed binding that the bootstrap verifies against the requested account identity.
4. Add a regression test preparing two configurations with the same provider ID and different account IDs/expected subjects. Prove a token for account A cannot be persisted under account B.
5. Fail preparation closed when an account-specific identity verifier is missing or mismatched; do not fall back to a provider-wide verifier for multi-account configurations.

## Boundaries

- The provider identity verifier itself fails closed when its returned subject differs from its configured expected subject.
- This report does not claim that every current host config supports multiple external accounts; the risk exists when a host uses the supported account_id dimension for multiple accounts under one provider ID.
- Provider/account IDs and verifier resolver implementations are host-owned, not supplied by an untrusted API caller.
- No runtime code was changed and no tests were executed during this source review.
