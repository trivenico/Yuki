# Phase 177 — OAuth refresh-token lifecycle coordinator

## Delivered

- Host-side refresh service reads an owner/provider/account-scoped grant from the encrypted vault.
- Fresh credentials are left untouched until their expiry enters the configured refresh-skew window.
- Expiring credentials are refreshed once through the pinned, form-encoded HTTPS transport.
- Token response shape, bearer type, lifetime, and granted scopes are validated before persistence.
- Vault compare-and-swap rotation binds the update to the current version and refresh-token fingerprint.
- If the provider omits a replacement refresh token, the vault retains the existing refresh token.
- Returned values contain metadata only; access and refresh tokens never enter the result.
- Ambiguous provider/network failures are not retried because the provider may already have rotated the token.

## Boundaries

- No scheduler, background worker, web route, or MCP token-provider wiring is enabled in this phase.
- Provider endpoints and OAuth client configuration remain trusted host configuration.
- Issuer/resource values are configured context, not cryptographically verified token claims.
- A CAS conflict is surfaced as a sanitized failure; the caller must re-read current credentials rather than retry the old token.
- Provider revocation, account identity verification, and disconnect orchestration remain separate work.
- No live platform account is connected.

## Validation

CI must validate the exact PR head. No local test execution is claimed.
