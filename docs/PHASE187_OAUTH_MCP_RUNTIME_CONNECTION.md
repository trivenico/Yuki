# Phase 187 — Owner-scoped OAuth-backed MCP runtime connection

## Delivered

- Add a host-side connection manager that composes the encrypted credential vault, expiry-aware refresh service and restricted MCP HTTP client.
- Bind each client to an authenticated owner, trusted provider and configured account.
- Resolve access tokens lazily through a private callback; no token is returned in connection metadata or tool results.
- Refresh immediately before MCP authorization is needed and read the current vault generation after refresh.
- Enforce required OAuth scopes against both refresh metadata and the stored credential.
- Require a non-empty explicit tool allow-list and a callable per-call approval gate.
- Preserve the MCP client's HTTPS host allow-list, DNS-pinned transport and prompt-injection scan.

## Security boundaries

The owner identifier must originate from the authenticated host session. Provider/account IDs, endpoint, host allow-list, tool allow-list and approver are host-owned configuration. Model-generated values must never select these trust boundaries.

This module does not create callback routes, perform OAuth discovery, or initiate an authorization flow. It assumes the owner has already completed the validated OAuth flow and that the supplied refresh service was configured for the same trusted provider. Refresh remains single-attempt; ambiguous failures are not retried. Financial or externally visible actions still require the platform-specific approval policy.

## Validation

Unit tests cover lazy credential resolution, owner/provider/account scoping, scope enforcement, missing credentials, refresh failure redaction, and mandatory approval/allow-list configuration. CI is authoritative; no local test execution is claimed.
