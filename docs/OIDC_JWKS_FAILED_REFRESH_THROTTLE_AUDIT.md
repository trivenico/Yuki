# OIDC JWKS Failed-Refresh Throttle Bypass Audit

## Finding

**Severity: Low — failed unknown-key refreshes bypass the per-issuer outbound-request throttle**

The JWKS cache is intended to throttle forced refreshes caused by an unknown signing-key ID. However, the cache updates its per-URI refresh timestamp only after the fetch succeeds and the returned document passes basic validation.

When the fetcher raises an exception or returns an invalid document, the timestamp is not advanced. Each subsequent verification attempt containing an unknown `kid` can therefore trigger another immediate outbound JWKS fetch. The configured minimum refresh interval does not constrain this failure path.

## Evidence

In `core/oidc_jwks_cache.py`:

1. `OIDCJWKSCache.get(..., force_refresh=True)` checks `_last_fetch[jwks_uri]` and returns the current fresh entry only when the last recorded fetch is within the refresh interval.
2. The cache then calls `self._fetcher(jwks_uri)` while holding the cache lock.
3. If the fetcher raises, the method raises `OIDCJWKSCacheError("oidc_jwks_unavailable")` before updating `_last_fetch`.
4. If the returned document is malformed, the method raises `OIDCJWKSCacheError("oidc_jwks_invalid")` before updating `_last_fetch`.
5. The timestamp is assigned only after the document passes the basic keys-list validation.
6. In `core/oidc_id_token.py`, an unknown `kid` triggers `get(..., force_refresh=True)` before the verifier rejects the token for an absent key. Thus repeated unknown-key tokens exercise the failed-refresh path.

## Impact and boundaries

An attacker able to submit ID-token-shaped inputs to an OIDC verification path can cause repeated outbound JWKS requests when the issuer endpoint is unavailable or returns invalid data. This can increase outbound traffic and contend for verifier/cache resources during an issuer outage.

This does not bypass signature verification: a token whose key ID is not present after refresh is still rejected. The impact is limited to availability and upstream request amplification. The exact reachability and rate depend on the surrounding callback/API rate limits, which were not established in this source-only review.

## Recommended remediation

1. Record a per-URI refresh-attempt timestamp before starting the forced fetch, not only after a successful fetch.
2. Consider separate success and failure backoff windows, with bounded exponential backoff and jitter for failures.
3. Ensure explicit administrative invalidation can reset the backoff when trusted key rotation or recovery requires it.
4. Add tests proving that repeated forced refreshes after fetch exceptions and malformed documents do not call the fetcher again before the configured interval.
5. Add a test that a later attempt succeeds after the backoff expires and that ordinary cached-key reads remain consistent with the no-stale-on-expiry policy.

## Existing protections

- JWKS entries have bounded TTL and LRU capacity.
- Concurrent cache misses are serialized.
- Returned documents are defensive copies.
- Unknown-key refreshes are throttled after successful fetches.
- Expired keys are not served after a failed refresh.
- The verifier rejects missing or ambiguous key IDs and validates the selected public key and token signature.

These controls remain useful but do not throttle repeated failed forced-refresh attempts.

## Scope and verification limits

Reviewed at PR #1346 head `2a59a75eb8483f9918cb39203d42fc0b26feadb4`:

- `core/oidc_jwks_cache.py`
- `core/oidc_id_token.py`
- `tests/test_oidc_jwks_cache.py`
- `docs/PHASE186_OIDC_JWKS_CACHE_ROTATION.md`

Source inspection only. No runtime test, outbound request measurement, API rate-limit analysis, or CI run was performed.
