# Phase 170 — Pinned OAuth HTTPS transport

## Objective

Provide a bounded JSON transport for OAuth metadata and token endpoints that closes the DNS-rebinding gap in the existing MCP HTTP client.

## Implemented

- Exact HTTPS host allow-list; no wildcard or implicit trust.
- HTTPS only, default port only, no URL userinfo, fragments, control characters, or untrusted hostnames.
- Resolve DNS once per request, reject the request if any returned address is non-global, and pin the TCP connection to one deterministic public IP from that answer set.
- TLS certificate validation and SNI continue to use the original hostname, not the pinned IP.
- No automatic redirects; all 3xx responses fail closed.
- GET and JSON POST for metadata-style endpoints.\n- Standard `application/x-www-form-urlencoded` POST for OAuth token endpoints, with bounded string fields and protected content-type handling.
- Bounded request and response bodies, JSON content-type enforcement, object-only JSON responses, and bounded timeout.
- Custom headers are validated; callers cannot override Host, connection framing, or content length.
- Stable sanitized transport errors do not include URLs, tokens, or response bodies.

## Integration constraints

- This transport is a primitive; it is not yet wired into metadata discovery, OAuth callback handling, or token exchange.
- Host allow-lists must come from reviewed platform configuration, never model or user-controlled arbitrary URLs.
- Do not automatically retry token exchange after ambiguous network failure: authorization codes may be single-use.
- Do not forward bearer credentials across origins or redirects.
- Token responses still require strict issuer/resource/audience/client binding and schema validation before any credential is persisted.
- This transport does not solve proxy environment trust, outbound firewall policy, or platform-specific callback URI approval. Production deployments should additionally enforce outbound network policy at the host/container boundary.

## Validation

Tests cover exact-host policy, endpoint parsing, private/mixed DNS rejection, pinned address selection, redirect rejection, request/response bounds, content type, JSON shape, header injection, and failure cleanup. CI is authoritative; no local test execution is claimed.
