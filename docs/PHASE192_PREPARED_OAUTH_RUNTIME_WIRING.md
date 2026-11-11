# Phase 192 — prepared OAuth registry runtime wiring

## Purpose

Connect the immutable host-prepared OAuth provider registry to the existing authenticated application runtime without requiring callers to manually duplicate resolver and catalog callbacks.

## Runtime contract

`build_conversation_runtime` accepts an optional `prepared_oauth_provider_registry`. When supplied, it derives both:

- `authorization_service_for_provider` from the registry's fail-closed provider resolver.
- `oauth_provider_catalog` from the registry's safe presentation projection.

The registry is mutually exclusive with explicit OAuth authorization resolver/catalog callbacks. This prevents ambiguous provider authority or accidental catalog drift.

The existing OAuth connection metadata registry and trusted disconnect resolver remain separately required. The prepared registry does not own connection metadata persistence or disconnect behavior.

## Security boundaries

- The runtime accepts only `PreparedOAuthProviderRegistry`, which itself accepts only trusted `PreparedOAuthProvider` instances.
- Unknown provider IDs resolve to no service.
- The authenticated OAuth API continues to derive the owner and session from the authenticated request.
- The catalog continues to expose only provider ID, display name, description, and availability.
- Runtime wiring does not initiate authorization, perform network discovery, exchange tokens, connect MCP, or enable tools.
- Provider construction remains an explicit host startup responsibility.

## Validation

Runtime tests cover resolver/catalog wiring, unknown provider fail-closed behavior, and rejection of simultaneous explicit resolver configuration. CI is authoritative; no local test execution is claimed.
