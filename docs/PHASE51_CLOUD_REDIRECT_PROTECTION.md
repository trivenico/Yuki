# Phase 51 — Credentialed cloud redirect protection

## Gap
Exact host allowlisting prevents sending credentials to arbitrary configured hosts, but HTTP redirect behavior is a separate trust boundary. A credentialed request should not automatically follow a redirect to a new destination.

## Change
- Add a redirect handler that refuses automatic redirects for OpenAI-compatible requests.
- Keep HTTPS endpoint validation and exact host allowlisting from Phase 50.
- Return sanitized request failures without logging the API key or prompt.
- Add unit tests for redirect refusal and sanitized error reporting.

## Operational note
Some providers may legitimately use redirects; this adapter intentionally fails closed in that case. Configure the documented canonical API endpoint instead of relaxing redirect handling globally. If redirects are ever needed, implement a provider-specific policy that validates every redirect target and never forwards credentials across origins.

## Limits
Tests use mocks and make no live requests. DNS compromise, proxy configuration, and network-level routing remain separate concerns; production deployments should use controlled egress. Passing this test is not a complete security certification.
