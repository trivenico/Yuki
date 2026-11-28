# OAuth Token Response Duplicate JSON Keys Audit

## Scope

Reviewed at PR #1346 head `eb2c6b922ea55a4b967fa7e39dad078a723b7d79`:

- `core/oauth_http_transport.py`
- `core/oauth_token_response.py`
- `core/oauth_authorization_service.py`
- `core/oauth_credential_vault.py`
- `tests/test_oauth_http_transport.py`
- `tests/test_oauth_token_response.py`
- `tests/test_oauth_authorization_service.py`

Source-level review only. No runtime tests were executed for this audit.

## Positive controls

The token response validator enforces:

- bounded opaque access and refresh token syntax;
- Bearer token type;
- integer `expires_in` bounded to one year;
- bounded and non-duplicated granted scopes;
- granted scopes as a subset of requested scopes;
- rejection of unknown response fields;
- bounded, syntactically compact ID tokens.

The authorization service verifies configured OIDC identity and provider account identity before calling the credential vault. The vault independently validates token syntax, expiry, and scope structure before persistence.

## Finding: duplicate JSON object names are silently collapsed

The HTTPS transport parses response bodies using Python's default `json.loads()`. The default decoder accepts duplicate object names and retains the last value. For example, a response such as:

```json
{"access_token":"first","access_token":"second","token_type":"Bearer","expires_in":3600}
```

is converted into an ordinary dictionary containing only the latter `access_token` value.

By the time `validate_oauth_token_response()` receives the document, duplicate-field evidence has been discarded. The validator therefore cannot reject ambiguous duplicate occurrences of security-sensitive fields such as `access_token`, `refresh_token`, `token_type`, `expires_in`, `scope`, or `id_token`.

### Impact and limits

This is a protocol strictness and interoperability hardening gap. It can cause inconsistent interpretation when a provider, intermediary, diagnostic tool, or another JSON implementation treats duplicate members differently. It is not evidence of an attacker bypassing TLS or directly controlling the trusted provider response. Exploitability depends on the upstream response path and any components that interpret or transform the same JSON differently.

## Recommendations

1. Parse JSON with an `object_pairs_hook` that rejects duplicate member names at every object depth.
2. Keep the duplicate-key rejection in the shared transport parser so discovery, registration, token, and other JSON responses receive consistent behavior.
3. Add transport tests for duplicate top-level and nested object keys, including duplicates of token security fields.
4. Ensure parse failures continue to surface only stable, non-sensitive transport errors.
5. Retain the existing token validator and vault validation as independent defense-in-depth layers.

## Severity and confidence

**Severity: Low-to-medium hardening.** No direct authorization bypass was demonstrated.

**Confidence: High** that the default JSON decoder silently collapses duplicate object names and that the token validator cannot detect them after parsing.

## Limitations

- Source inspection only; no runtime tests or provider integration tests were run.
- No claim is made that a currently configured OAuth provider emits duplicate fields.
