# Authorization Header Duplicate-Parsing Audit

## Scope

Source review of bearer-session extraction across the ASGI API surfaces at PR #1346 head `eea921126b4386b60a21fff56999b367dca3387e`.

Reviewed:
- `api/conversations.py`
- `api/inference.py`
- `api/evaluations.py`
- `api/income.py`
- `api/oauth_connections.py`
- `api/mcp_management.py`
- `api/oauth_authorization.py`
- `core/auth.py`

## Finding: inconsistent handling of duplicate Authorization fields

**Severity: P2 security hardening / request-authentication ambiguity**

ASGI represents request headers as an ordered list of byte-pair fields and preserves duplicate fields. Several Yuki API authentication helpers convert that list into a dictionary:

```python
headers = {
    key.lower(): value for key, value in scope.get("headers", [])
    if isinstance(key, bytes) and isinstance(value, bytes)
}
raw = headers.get(b"authorization", b"").decode("latin-1")
```

This pattern appears in the conversation, inference, evaluation, income, and OAuth-connections APIs. When more than one Authorization field is present, the dictionary comprehension silently keeps the last value. The request is therefore authenticated using an arbitrary winner rather than rejected as ambiguous.

The OAuth-authorization and MCP-management APIs use a safer pattern: collect Authorization values and require exactly one. This creates inconsistent authentication semantics across routes in the same application.

### Security impact

HTTP intermediaries and application servers can differ in how they reject, preserve, combine, or select duplicate Authorization fields. A request that is interpreted as one credential by a proxy, gateway, logging layer, or policy enforcement point may be interpreted as another credential by an endpoint using last-value-wins parsing. This can undermine assumptions that all layers authenticated the same principal, especially where gateways perform authorization or audit attribution.

This review confirms the parsing inconsistency and the last-value-wins behavior in application code. It does **not** establish that the deployed proxy chain currently permits an exploitable bypass; that requires an end-to-end deployment test.

## Evidence

- `api/conversations.py`: `_principal` builds a dict from the ASGI header list before reading Authorization.
- `api/inference.py`: `_bearer` uses the same dict-comprehension approach.
- `api/evaluations.py`: `_headers` collapses duplicate fields into one value; `_user` consumes it.
- `api/income.py`: `_user` collapses duplicate fields.
- `api/oauth_connections.py`: `_user` collapses duplicate fields.
- `api/oauth_authorization.py` and `api/mcp_management.py`: require exactly one Authorization field and reject duplicates.
- `core/auth.py`: session tokens are opaque random bearer secrets; only SHA-256 digests are persisted, and resolution checks expiry, revocation, and disabled-user state.

## Recommended remediation

1. Centralize bearer extraction in one shared helper used by every API surface.
2. Collect all Authorization fields case-insensitively and require exactly one field; reject zero or multiple fields.
3. Parse the scheme and credential strictly, reject leading/trailing whitespace and embedded whitespace, and retain the existing token-length bound.
4. Ensure reverse proxies and ingress layers reject duplicate Authorization fields rather than silently merging them.
5. Add ASGI regression tests that supply two Authorization fields in both orders, including one valid session token and one invalid token. Assert that every protected API rejects both arrangements without invoking route handlers.
6. Add an integration test through the actual ingress/proxy stack to verify that duplicate fields cannot be normalized differently between layers.

## Verification limitations

This was a source-only review. No runtime tests, proxy integration tests, CI workflows, or deployment configuration were executed or verified. The impact is therefore classified as an authentication parsing ambiguity with a deployment-dependent exploitability condition, not as a demonstrated authentication bypass.
