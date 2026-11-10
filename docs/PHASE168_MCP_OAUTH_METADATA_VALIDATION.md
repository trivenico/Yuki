# Phase 168 — MCP OAuth metadata validation

## Objective

Add a pure validation and discovery-planning layer for the current MCP OAuth authorization model. This is the next step after Phase 167's PKCE primitives. It does not perform HTTP requests or create credentials.

## Implemented

- Protected Resource Metadata (RFC 9728) discovery URL candidates: path-specific first, then root fallback.
- Authorization Server Metadata (RFC 8414) and OpenID Connect discovery URL candidates for issuers with paths.
- Exact resource identifier matching in Protected Resource Metadata.
- Explicit authorization-server issuer binding in Authorization Server Metadata.
- HTTPS-only metadata endpoint validation; rejects user-info, non-default ports, fragments, and unexpected query components.
- Bounded and typed metadata arrays and capability flags.
- Requires PKCE S256 support; no downgrade to `plain`.
- Registration strategy follows MCP priority: pre-registered client, Client ID Metadata Document support, then Dynamic Client Registration when advertised.
- Fails closed if no client registration method is available.

## Deliberate non-goals

- No HTTP metadata fetcher, redirect handling, DNS resolution, or network pinning.
- No parsing of `WWW-Authenticate` challenges yet.
- No selection among multiple authorization servers; owner/platform policy must select a reviewed issuer explicitly.
- No CIMD document hosting, DCR registration request, callback server, transaction persistence, code exchange, token lifecycle, or account linking.
- No arbitrary external OAuth host is enabled.
- Upwork remains disabled pending the exact callback URI approval and complete OAuth/token integration.

## Security requirements before network integration

The HTTP layer must enforce bounded response sizes, JSON content types, timeouts, redirect rejection or tightly constrained redirect validation, DNS/IP safety and connection pinning, issuer/resource binding, and no credential forwarding across origins. Metadata is untrusted input until validated by these pure functions.

## Validation

`tests/test_mcp_oauth_metadata.py` covers discovery URL construction, exact resource and issuer binding, endpoint URL validation, capability types, S256 enforcement, registration priority, and fail-closed behavior. CI on the stacked pull request is authoritative; no local execution is claimed.

## References

- MCP Authorization, 2025-11-25: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- MCP Authorization, current specification draft: https://modelcontextprotocol.io/specification/draft/basic/authorization
- RFC 9728: https://www.rfc-editor.org/rfc/rfc9728
- RFC 8414: https://www.rfc-editor.org/rfc/rfc8414
