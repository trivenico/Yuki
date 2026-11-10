# Phase 178 — DNS-pinned MCP HTTP transport

## Delivered

- Replace MCP client's urllib transport with a dedicated pinned HTTPS transport.
- Resolve the allow-listed hostname once per request and connect to the selected public IP directly.
- Preserve the original hostname for TLS SNI and certificate verification.
- Normalize IPv4-mapped IPv6 before public-address checks.
- Reject non-global destinations, redirects, non-HTTPS endpoints, unapproved hosts, malformed headers, and oversized request/response bodies.
- Keep bearer credentials injected at request time by trusted host code.
- Validate JSON-RPC response IDs against the outstanding request ID.

## Boundaries

- JSON MCP responses only; SSE-only response handling remains unsupported.
- DNS resolution itself uses the system resolver and is not independently time-bounded by the transport's socket timeout.
- No proxy support is enabled; the transport connects directly to the pinned IP.
- Exact host allow-lists remain required. Public IP validation is not a substitute for reviewing the provider endpoint.
- Tool allow-lists and explicit per-call approval remain enforced.
