# OAuth Dynamic Client Registration Response Capability Validation Audit

## Scope

Reviewed at PR #1346 head `8527768336da4448f6cce274d1e18bc9c0dc872b`:

- `core/mcp_oauth_client_registration.py`
- `core/mcp_oauth_provider_bootstrap.py`
- `core/mcp_oauth_metadata.py`
- `core/oauth_http_transport.py`
- `tests/test_mcp_oauth_client_registration.py`
- `tests/test_mcp_oauth_discovery.py`

Source-level review only. No runtime tests were executed.

## Existing controls

The registrar validates the selected registration plan, HTTPS redirect URI, requested scopes, client name, registration endpoint equality, registration-host allowlist, bounded client ID, public-client authentication method, absence of a non-empty client secret, and exact redirect URI equality when the provider returns that field.

The DCR request explicitly asks for:

- `grant_types: [authorization_code, refresh_token]`
- `response_types: [code]`
- `token_endpoint_auth_method: none`

The resulting client ID is then used to construct the authorization service.

## Finding: returned grant and response capabilities are not validated

After the DCR POST, `PublicOAuthClientRegistrar.register()` checks the returned `client_id`, `token_endpoint_auth_method`, `client_secret`, and optional `redirect_uris`. It does not validate returned `grant_types` or `response_types`.

If the provider includes those members but returns values inconsistent with the request, the registrar accepts the response and constructs a client registration as if the requested authorization-code configuration had been issued.

This leaves the locally assumed client capabilities different from the server-reported registration. A provider response that includes broader grant capabilities may also mean the client is registered for flows the application did not request.

### Impact and limits

This is a client-registration contract and least-privilege validation gap. The application itself still initiates authorization-code flow and uses PKCE; this review does not establish that the application invokes an unintended grant. The provider controls its own registration behavior, and practical impact depends on how the provider interprets and enforces the returned registration.

The absence of these fields should be handled according to the applicable registration protocol defaults; the finding concerns fields that are present but contradictory or malformed.

## Recommendations

1. When `grant_types` is present, require a well-formed list consistent with the supported application flow and reject unrequested grants.
2. When `response_types` is present, require the expected `code` response type and reject unexpected values.
3. Validate response field types, duplicates, and bounds before accepting the registration.
4. Consider validating a returned `scope` against the requested scope set and explicitly handling any narrower grant rather than assuming all requested scopes were registered.
5. Add tests for malformed types, duplicate values, unexpected grants, missing authorization-code capability, unexpected response types, and narrower returned scopes.
6. Keep the shared duplicate-JSON-key parser remediation from `OAUTH_TOKEN_RESPONSE_DUPLICATE_JSON_KEYS_AUDIT.md`; it applies to registration and metadata documents as well.

## Severity and confidence

**Severity: Low-to-medium hardening.** No direct authorization bypass was demonstrated.

**Confidence: High** that present `grant_types` and `response_types` values are currently not inspected by the registrar.

## Limitations

- Source inspection only; no runtime or provider integration tests were executed.
- No claim is made that a currently configured provider returns contradictory registration capabilities.
