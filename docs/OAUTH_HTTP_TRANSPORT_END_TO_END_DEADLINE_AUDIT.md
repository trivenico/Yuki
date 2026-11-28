# OAuth HTTP Transport End-to-End Deadline Audit

## Scope

Source-only review at PR #1346 head `c540f2e79888935e11b4f8a9fe0de49ebe4c6245`.

Reviewed:

- `core/oauth_http_transport.py`: `PinnedOAuthHTTPTransport.request_json()`, `request_form()`, `request_form_status()`, DNS resolution and pinned HTTPS connection setup
- `tests/test_oauth_http_transport.py`
- `tests/test_oauth_form_transport.py`
- `docs/PHASE170_PINNED_OAUTH_HTTP_TRANSPORT.md`

No tests, DNS calls, or live OAuth requests were executed.

## Finding: configured socket timeout does not bound total OAuth request duration

**Severity: Moderate (P2) reliability and resource-exhaustion risk**

### Evidence

1. The transport accepts a timeout between 1 and 30 seconds and passes it to the pinned HTTPS connection factory.
2. DNS resolution is performed synchronously by `_resolve_public_ip()` through the injected resolver (by default `socket.getaddrinfo`). No elapsed-time deadline or cancellation boundary is applied to this resolution step.
3. After connection, `request_json()` and `request_form()` call `response.read(MAX_RESPONSE_BYTES + 1)`. The byte cap bounds response size, but no monotonic deadline is checked before or during reads.
4. `request_form_status()` similarly performs a bounded read of up to 16,385 bytes, without a total elapsed-time deadline.
5. The connection timeout is a blocking socket-operation timeout, not a deadline for DNS resolution plus connect, TLS, response headers, and the complete body. A peer that periodically makes read progress can keep a response alive beyond the configured timeout, while a slow system resolver can delay the request before the socket timeout applies.
6. The tests verify request/response size limits, DNS address policy, redirect rejection and cleanup, but do not verify a total wall-clock deadline or bounded resolver duration.

### Impact

An OAuth metadata, registration, token, or revocation request can occupy a worker beyond the configured 1–30 second timeout. A slow-drip response can extend the body-read lifetime while remaining within the response byte limit. A stalled system DNS lookup can also hold the calling worker before connection establishment.

This is especially relevant to authorization and refresh flows, where requests may be initiated by authenticated users or background lifecycle operations. It can reduce worker capacity and delay shutdown or disconnect completion under an unresponsive or adversarial endpoint/resolver.

The response byte caps, exact-host allowlist, public-IP validation, IP pinning, TLS hostname verification, redirect rejection and sanitized errors remain useful controls. This finding does not indicate an SSRF bypass or token disclosure.

### Recommended remediation

1. Introduce a monotonic end-to-end deadline before DNS resolution, and carry its remaining budget through DNS, connect, TLS, response headers and body reads.
2. Ensure DNS resolution is itself bounded or cancellable; if the platform resolver cannot provide that contract, isolate it behind a bounded resolver mechanism rather than assuming the socket timeout covers it.
3. Before each blocking network read, cap the read timeout to the smaller of the configured inactivity timeout and remaining total budget. Recompute the remaining budget after each read.
4. Apply identical deadline semantics to JSON, form, and revocation-status requests.
5. On deadline expiry, close the connection and raise a stable timeout-specific `OAuthHTTPError` without exposing endpoint or credential material.
6. Add deterministic tests with a resolver that blocks beyond the deadline and a local slow-drip HTTP/TLS peer that sends bytes at intervals shorter than the inactivity timeout. Assert bounded elapsed time and connection cleanup.

### Boundaries and confidence

The missing total deadline is directly visible in the inspected implementation. Slow-drip and resolver-stall impacts follow from the blocking call structure and socket-timeout semantics; neither was reproduced in this review.

No runtime code was changed. Tests, CI, and live provider behavior were not exercised.
