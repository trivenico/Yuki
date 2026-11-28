# OAuth Opaque Token Length-Boundary Audit

## Finding

**Severity: Low — declared token-size limit can be bypassed with trailing padding characters**

The OAuth token-response validator and credential vault both use an opaque-token regular expression intended to limit the token's non-padding portion to 8,192 characters:

`^[A-Za-z0-9._~+/-]{1,8192}={0,}$`

Because the trailing padding group is unbounded, the expression does not impose an 8,192-character maximum on the complete token. A value consisting of a valid one-character token prefix followed by a long run of `=` characters passes both validators.

The vault's AES-GCM adapter separately limits plaintext to 16,384 bytes. Consequently, values beyond that later limit fail during encryption, but values between 8,193 and 16,384 characters can pass the nominal 8,192-character policy and be persisted.

## Evidence

- `core/oauth_token_response.py`: `_TOKEN` uses the unbounded `={0,}` suffix. `_opaque_token()` relies on `fullmatch()` and does not independently check total length.
- `core/oauth_credential_vault.py`: its separate `_TOKEN` expression has the same unbounded suffix. `_token()` likewise relies only on `fullmatch()`.
- `core/oauth_aead.py`: `AESGCMCredentialCipher._MAX_PLAINTEXT` is 16,384 bytes, which is a later and different boundary.
- `tests/test_oauth_token_response.py`: oversized-token tests cover a long run of ordinary token characters, but do not cover a valid prefix followed by excessive padding.
- `core/oauth_http_transport.py`: token responses are bounded at the HTTP layer to 1,000,000 bytes, which limits the total response but does not enforce the intended per-token limit.

## Impact and boundaries

A provider response can supply a token larger than the documented/intended 8,192-character limit while still passing both token validators. For values within the vault's 16,384-byte encryption limit, the larger token can be encrypted and stored. This creates inconsistent resource and input policy across validation and persistence boundaries.

This is not an authentication bypass, scope escalation, plaintext disclosure, or unbounded-memory condition: the HTTP response and encryption plaintext are separately bounded. The finding concerns enforcement of the declared token-size policy and consistency between layers.

## Recommended remediation

1. Enforce total token length explicitly before regex matching, for example `1 <= len(value) <= 8192`, in both the response validator and vault.
2. Keep the grammar check separate from the total-length check so padding syntax cannot bypass the maximum.
3. Add regression cases for access and refresh tokens with a valid one-character prefix followed by 8,192 or more `=` characters.
4. Add a vault-level test proving the same oversized padded values are rejected before encryption.
5. Consider defining the token-size limit once in a shared validation primitive to prevent drift between response parsing and persistence.

## Review scope and limitations

Reviewed at PR #1346 head `518a1e9bfabcdcc7d6eadcc0d36be0516ba678cd`:

- `core/oauth_token_response.py`
- `core/oauth_credential_vault.py`
- `core/oauth_aead.py`
- `core/oauth_http_transport.py`
- `core/oauth_authorization_service.py`
- `core/oauth_refresh_service.py`
- `tests/test_oauth_token_response.py`
- `tests/test_oauth_authorization_service.py`
- `tests/test_oauth_refresh_service.py`
- `tests/test_oauth_credential_vault.py`

Source inspection only. No runtime tests were executed and no exploit reproduction was run.