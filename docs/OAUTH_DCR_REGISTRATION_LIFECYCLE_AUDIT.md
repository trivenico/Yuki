# OAuth Dynamic Client Registration Lifecycle Audit

## Scope

Source review at PR #1346 head `891fd35708dbdec9762435b0044a52e5b88f6a3f`. Reviewed metadata discovery, client registration, provider bootstrap, prepared-provider registry wiring, related tests, and the documented host-startup integration contract.

## Finding: DCR client registrations are not persisted or reused, so provider preparation can create orphaned remote clients

**Severity: Medium — remote resource leakage and provider-registration exhaustion (provider-dependent).**

The documented integration contract tells host startup code to create one `OAuthProviderBootstrapConfig` per provider and call `MCPProviderOAuthBootstrap.prepare()`. For the DCR path, each call performs a new registration POST. The bootstrap and registrar do not persist a successful client registration, accept a previously registered client ID from durable state unless explicitly configured as pre-registered, or provide a registration lifecycle manager.

Consequently, each process restart or repeated preparation for the same provider can create another remote public client. The resulting client ID is held only in the returned in-memory `PreparedOAuthProvider`. A subsequent restart cannot recover or reuse it from this code path and may register another client. Providers that retain DCR clients can accumulate registrations until provider-side cleanup or quotas intervene.

There is also a failure window after the remote POST succeeds: the registrar validates the response locally and raises on malformed or policy-incompatible fields (for example, an unexpected `client_secret`, a mismatched returned redirect URI, or an invalid client ID). It does not retain registration-management metadata or attempt remote cleanup. The provider may therefore retain a client that the host refuses to publish. If the response is lost after provider-side creation, the code intentionally avoids automatic retry, but no reconciliation path is exposed to resolve the ambiguous outcome.

## Source evidence

- `docs/PHASE190_MCP_OAUTH_PROVIDER_BOOTSTRAP.md`: instructs host startup code to call `MCPProviderOAuthBootstrap.prepare` for each provider.
- `core/mcp_oauth_provider_bootstrap.py`: every `prepare()` call performs discovery and invokes a new `PublicOAuthClientRegistrar.register()`; no cache or durable registration store is consulted.
- `core/mcp_oauth_client_registration.py`: DCR always issues one POST. It returns only method, client ID, redirect URIs, and scopes; it does not retain a registration access token or management URI for later deletion/update.
- The same registrar raises after the POST if the response contains an unexpected client secret, mismatched redirect URI, or invalid client ID, without a cleanup attempt.
- `core/oauth_provider_registry.py` stores prepared services only in an immutable in-memory mapping; it is not a durable registration store.
- `tests/test_mcp_oauth_provider_bootstrap.py` and `tests/test_mcp_oauth_client_registration.py` verify one registration per prepare and no automatic retry after transport failure, but do not cover repeated preparation/restart reuse, remote cleanup, or reconciliation.

## Impact and conditions

The confirmed behavior is repeated DCR on each invocation of `prepare()`. Persistent accumulation depends on the authorization server retaining registrations and not automatically expiring or deduplicating them. The exact provider-side quota, retention, and cleanup behavior is not established by this repository review.

The issue is not that untrusted callers can trigger registration through the authenticated user API: preparation is a host-side startup operation. The exposure is operational lifecycle leakage and potential provider-side registration quota exhaustion when startup/reload/recovery invokes preparation repeatedly.

## Recommended remediation

1. Persist a provider-registration record keyed by a stable host-owned identity (provider ID plus issuer/resource and registration configuration fingerprint) before publishing the prepared provider.
2. On startup, reuse a validated stored client registration rather than unconditionally issuing DCR. Define explicit rotation/re-registration behavior when issuer, redirect URI, scopes, or application type changes.
3. Where the provider returns registration management credentials/URI, validate and store them securely and implement an explicit, bounded deregistration/cleanup lifecycle. Do not assume every provider supports deletion.
4. For ambiguous POST outcomes, record a durable `registration_pending_reconciliation` state and require operator/provider reconciliation before another DCR attempt; do not blindly retry.
5. Separate remote registration from in-memory provider publication so a failed local preparation can be resumed without creating a second remote client.
6. Add tests that invoke prepare twice with the same config and assert registration reuse, simulate a successful remote creation followed by a malformed response, and exercise crash/restart recovery and provider cleanup where supported.

## Non-findings and boundaries

- Metadata and registration endpoints are explicitly HTTPS/host-allow-listed before the DCR POST.
- DCR is selected only by the validated registration plan; pre-registered and CIMD paths do not POST.
- Registration transport failure is sanitized and not automatically retried, which avoids blind duplicate registration after an ambiguous outcome but does not provide reconciliation.
- No production provider retention or quota behavior was inferred.
- No runtime code was changed and no tests were executed during this source review.
