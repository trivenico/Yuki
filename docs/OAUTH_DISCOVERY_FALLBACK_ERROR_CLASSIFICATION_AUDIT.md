# OAuth Metadata Discovery Fallback Error Classification Audit

## Scope

Reviewed PR #1346 at head `c04a1492793cd49f00deb74a7495f7e99428dfc5`:

- `core/mcp_oauth_discovery.py`
- `core/oauth_http_transport.py`
- `core/mcp_oauth_metadata.py`
- `tests/test_mcp_oauth_discovery.py`
- `tests/test_mcp_oauth_metadata.py`

Source-level review only. No runtime tests were executed.

## Finding

**Metadata discovery retries the alternate well-known URL after transport policy and response-validation failures, not just transient endpoint unavailability.**

### Evidence

1. `TrustedMCPOAuthDiscovery._fetch_document()` invokes the injected `request_json("GET", url)` transport inside a broad `except Exception` block.
2. Every exception is replaced with `OAuthMetadataError("metadata_endpoint_unavailable")`, discarding the original failure category.
3. `_fetch_first_available()` treats that one error as retryable and attempts the next candidate URL.
4. The pinned transport deliberately raises distinct errors for rejected redirects, blocked DNS addresses, oversized responses, invalid content types, invalid JSON, and other transport failures.
5. Existing discovery tests cover fallback on network unavailability and terminal failure for a malformed *decoded metadata document*, but do not cover transport-policy failures.

This contradicts the comment in `_fetch_first_available()` that only transport unavailability permits trying the next standard URL.

### Impact

A metadata endpoint that returns a redirect, oversized body, invalid content type, or malformed JSON can cause discovery to continue to the alternate well-known URL rather than fail closed at the first endpoint.

The alternate URL remains HTTPS-host-allow-listed and its decoded metadata is still validated against the expected resource or issuer. Therefore this finding does **not** demonstrate arbitrary endpoint injection or a direct SSRF bypass. The concrete defect is that security-policy and response-integrity failures are incorrectly downgraded into availability failures, weakening the intended fail-closed discovery contract and potentially hiding provider misconfiguration or hostile behavior at the preferred metadata endpoint.

### Recommended remediation

1. Preserve transport error categories across the discovery boundary. Catch only explicitly classified transient connection/unavailability errors as retryable.
2. Treat redirect rejection, address-policy rejection, response-size violations, content-type violations, JSON decoding failures, and other protocol/policy errors as terminal.
3. Avoid classifying arbitrary injected-transport exceptions as retryable; define a narrow transport exception contract.
4. Add discovery tests proving that only connection-level unavailability tries the alternate URL, while each policy/protocol failure terminates after one request.

## Severity and confidence

**Severity: Low to Medium** — fail-closed policy inconsistency and protocol-integrity downgrade; no direct attacker-controlled endpoint bypass demonstrated.

**Confidence: High** — the broad exception conversion and retry condition are explicit in the reviewed source.

## Limitations

- Source inspection only; no runtime tests or provider integration tests were run.
- The actual security impact depends on provider behavior and which transport errors occur at runtime.
