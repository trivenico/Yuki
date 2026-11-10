# Phase 182 — OAuth authorization-response issuer validation

## Delivered

- Extend the PKCE callback validator to accept the RFC 9207 iss parameter.
- Compare any supplied issuer exactly against the trusted authorization-server issuer.
- Allow host configuration to require iss when authorization-server metadata advertises authorization-response issuer support.
- Reject missing, duplicated, or mismatched issuer values before consuming the authorization transaction or exchanging the code.
- Repeat the issuer check against the consumed transaction's issuer before token exchange.
- Preserve compatibility with providers that do not advertise RFC 9207 support; an unsolicited issuer parameter is still checked if present.

## Security boundary

The capability flag and expected issuer must come from validated authorization-server metadata and trusted host configuration. They must never be selected by the model or callback contents. This protects the authorization-code flow against authorization-server mix-up when the provider supports RFC 9207.
