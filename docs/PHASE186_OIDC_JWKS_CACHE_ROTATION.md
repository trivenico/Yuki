# Phase 186 — Bounded OIDC JWKS caching and rotation

## Delivered

- Add a thread-safe JWKS cache keyed by the exact trusted JWKS URI.
- Bound cache lifetime (default five minutes) and capacity (default 32 issuers).
- Return defensive copies so verifier callers cannot mutate cached key material.
- Coalesce concurrent cache misses for the same URI.
- On an unknown signing-key ID, request a forced refresh, throttled to one refresh per URI every five seconds.
- Reject malformed JWKS documents and never fall back to expired/stale keys when a refresh fails.
- Add explicit cache invalidation for trusted host lifecycle operations.
- Integrate the cache into OIDC ID-token verification while retaining the pinned transport as its fetch boundary.

## Security and operational boundaries

The cache accepts only the verifier's host-configured JWKS URI; JWT headers cannot choose a URI. The fetch callback must remain the pinned OAuth HTTPS transport. The cache does not itself establish issuer trust or validate signatures; the verifier continues to validate key type, key usage, algorithm, key size and token claims.

A provider key rotation may take up to the refresh throttle interval to become visible after an unknown-kid response. Expired entries are not served on network failure. No stale-while-revalidate behavior is enabled.

## Validation

Added tests for expiry, refresh throttling, defensive copies, bounded LRU eviction, malformed documents and concurrent cache misses. CI is authoritative; no local test execution is claimed.
