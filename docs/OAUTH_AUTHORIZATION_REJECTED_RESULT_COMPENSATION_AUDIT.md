# OAuth authorization completion: rejected-result compensation gap

## Finding

**Severity: Medium — orphaned credential after provider resolver misconfiguration**

The authenticated callback API does not compensate a credential persisted by the authorization service when it rejects the service result before attempting connection-registry registration.

## Evidence

- `core/oauth_authorization_service.py`, `OAuthAuthorizationService.complete()`: after consuming the owner/session-bound transaction, validating the callback and token response, verifying provider identity, it calls `self._vault.store(... provider_id=self._provider_id, account_id=self._account_id ...)`. It then returns provider/account metadata and the credential version.
- `api/oauth_authorization.py`, callback path: the API resolves the service using the route's provider ID, calls `service.complete()`, then validates that `result["provider_id"] == provider_id`.
- If that provider equality check fails, the API immediately returns `502 authorization_completion_failed`. The compensation method `discard_unregistered_grant()` is only called in the later registry-registration exception handler.
- `tests/test_oauth_authorization_api.py::test_provider_mismatch_does_not_register_connection` verifies the registry remains empty, but does not assert that compensation is attempted.
- The existing `discard_unregistered_grant()` uses an expected credential version, so it can avoid deleting a newer generation when compensating a specific stored grant.

## Failure sequence

1. A callback arrives at route provider B.
2. A misconfigured trusted resolver returns an authorization service configured for provider A.
3. The service successfully exchanges the code, verifies provider A's account identity, and persists the credential under provider A/account X.
4. The API sees the returned provider A does not match route provider B and returns an error.
5. The vault credential remains, but the API never registers a corresponding connection record.

This is a host-configuration failure scenario, not evidence that a remote caller can choose arbitrary provider endpoints or forge the service result. The resolver and service are trusted host dependencies. Nevertheless, the API's own provider-mismatch guard currently rejects the result without cleaning up the side effect that has already occurred.

## Impact

A credential may remain durably stored but undiscoverable through the connection registry. It is not necessarily usable through the normal registry-driven UI, but it is an orphaned secret and can block a later authorization attempt for the same owner/provider/account because the vault rejects duplicate credential storage. It also creates inconsistent operational state that requires explicit cleanup.

## Recommended remediation

- On every invalid completion result after `service.complete()` has returned, attempt version-fenced compensation before returning an error, provided the returned account ID and credential version are valid.
- Treat compensation failure as a distinct recovery-required outcome; do not report ordinary rejection as though no credential side effect occurred.
- Keep the service's own provider identity authoritative for the compensation operation. Do not pass an untrusted provider ID into the vault cleanup path.
- Add a regression test where the route provider differs from the service's configured provider: assert the service's version-fenced discard method is called and the connection registry remains unchanged.
- Add tests for malformed completion metadata after successful persistence, including compensation failure.
- Consider making the service resolver validate that its configured provider identity matches the requested provider before invoking `complete()`. This is useful defense in depth but does not replace compensation for post-persistence failures.

## Scope and confidence

The source-level control-flow gap is confirmed. The concrete trigger requires trusted host resolver misconfiguration or an invalid service implementation/result. No evidence of an externally exploitable provider-selection bypass was found. No runtime tests were executed as part of this source review.
