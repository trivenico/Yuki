# Phase 166 — Authenticated remote MCP transport boundary

## Objective

Allow a trusted application host to attach short-lived OAuth bearer credentials to an already reviewed remote MCP endpoint without exposing tokens to configuration, skill arguments, agent prompts, tool results, or logs.

## Contract

- `MCPHTTPClient` accepts an optional callable token provider injected by trusted host code.
- The provider is invoked for each HTTP request, allowing an external OAuth broker to refresh or rotate credentials without reconstructing the MCP client.
- Tokens are validated against a bounded bearer-token character set and are sent only in the HTTP `Authorization: Bearer` header over the existing HTTPS-only transport.
- Provider exceptions and malformed values fail closed with stable errors that do not include the token or provider exception text.
- When no provider is configured, existing public/unauthenticated MCP behavior remains unchanged.
- MCP server configuration rejects unknown fields. The MCP skill rejects unknown request fields, including raw credential injection, and only accepts providers through trusted constructor injection.
- Skill request text is bounded to 64 KB.

## Security boundaries and limitations

- This phase does not implement OAuth authorization, dynamic client registration, PKCE, callback handling, refresh-token storage, revocation, or account linking.
- A token provider must be supplied by a future authenticated OAuth/secret-broker integration; environment variables or model-supplied values are not a production credential lifecycle.
- This transport does not itself approve tool calls. Explicit tool allow-lists, exact per-call confirmation, skill-level approval, and untrusted-result handling remain mandatory.
- Redirects remain disabled, endpoint host allow-lists remain required, and MCP responses remain bounded.
- The DNS validation/rebinding limitation of the existing URL transport is not addressed in this phase and must be resolved before treating arbitrary configurable MCP hosts as production-safe.

## Upwork integration note

Upwork's official MCP endpoint advertises OAuth 2.1 with dynamic client registration. A production Yuki connector must separately implement the authorization-code/PKCE lifecycle, approved redirect URI registration, token refresh/revocation, account linking, and platform-specific tool policy. Begin with read-only discovery and keep all proposal, messaging, publication, contract, and financial actions behind exact owner approvals. Do not substitute browser automation or scrape Upwork pages.

## Acceptance

Tests cover bearer header injection, provider rotation per request, no-auth compatibility, malformed tokens, provider failures, credential injection rejection, bounded skill input, and absence of token leakage in stable errors.
