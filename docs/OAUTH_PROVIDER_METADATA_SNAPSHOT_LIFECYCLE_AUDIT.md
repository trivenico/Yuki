# OAuth Provider Metadata Snapshot Lifecycle Audit

## Scope

Reviewed at PR #1346 head `0421b32d3aef87ed973df9a3400f62a4246852e1`:

- `core/mcp_oauth_provider_bootstrap.py`
- `core/mcp_oauth_discovery.py`
- `core/mcp_oauth_metadata.py`
- `core/oauth_provider_registry.py`
- `api/runtime.py`
- `tests/test_mcp_oauth_provider_bootstrap.py`
- `tests/test_mcp_oauth_discovery.py`

This is a source-level review. No runtime tests were executed as part of this audit.

## Finding

**Prepared OAuth authorization services retain provider metadata snapshots without a defined refresh or replacement lifecycle.**

### Evidence

1. `MCPProviderOAuthBootstrap.prepare()` performs metadata discovery and constructs an `OAuthAuthorizationService` with the discovered authorization endpoint, token endpoint, issuer, and resource.
2. `OAuthAuthorizationService` stores these endpoint values as instance configuration and uses them for authorization initiation and token exchange.
3. `PreparedOAuthProviderRegistry` stores the prepared service instances in an immutable mapping.
4. The reviewed registry and runtime composition expose no metadata expiry, periodic rediscovery, explicit invalidation, or atomic prepared-service generation replacement mechanism.

### Impact

If a provider changes or retires its advertised authorization or token endpoint after preparation, an already-running application can continue using the old endpoint configuration.

This can cause authorization failures and prolonged use of stale provider routing. If an old endpoint is reassigned or its security properties change, the consequences could be more serious; that scenario is provider- and deployment-dependent and was not demonstrated in this source review.

The finding does **not** establish an attacker-controlled endpoint injection: discovery and endpoint host allowlists provide meaningful protections. The issue is freshness and lifecycle management of otherwise validated metadata.

### Why this is distinct from credential configuration binding

The existing OAuth provider configuration/credential binding audit addresses credentials being reused across changed issuer, resource, or client configuration. This finding concerns the active authorization service's metadata snapshot and the absence of a defined refresh/replacement lifecycle. They are related but different boundaries.

## Recommendations

1. Add an explicit metadata freshness policy to prepared providers, including a bounded maximum age and a clear stale/unavailable state.
2. Provide an explicit refresh operation that re-runs trusted discovery and validates the full metadata document before publishing a replacement service.
3. Publish refreshed services atomically with a monotonically increasing provider configuration generation. Requests already in flight should finish against their captured generation; new requests should use the new generation.
4. Bind pending authorization transactions to the provider metadata/configuration generation that created them. On completion, either use a deliberately supported old generation or reject and restart; do not silently mix endpoint generations.
5. Coordinate credential provenance and service generation so that endpoint/issuer/client changes cannot silently reuse incompatible credentials. This should complement, not replace, the existing credential-binding remediation.
6. Add tests for endpoint changes between preparation and authorization, endpoint changes between begin and callback, invalid refreshed metadata, failed refresh, and concurrent refresh publication.

## Severity and confidence

**Severity: Medium operational/security hardening**, with deployment-dependent escalation if retired endpoints can be reassigned or their trust properties change.

**Confidence: High** that prepared services are snapshots and that the reviewed code has no automatic metadata refresh/replacement lifecycle. Exploitability beyond stale endpoint use is not established.

## Limitations

- Source inspection only; no runtime tests or provider integration tests were run.
- No claim is made about external load balancers, DNS controls, or operational restart automation outside this repository.
