# Phase 50 — Cloud endpoint trust boundary

## Finding
The OpenAI-compatible adapter sends a bearer API key to its configured HTTPS endpoint. HTTPS alone does not establish that the endpoint is trusted: a typo, hostile config change, or malicious gateway could receive the credential.

## Change
- Restrict cloud endpoint hostnames to a small explicit allowlist of supported provider/gateway hosts.
- Require HTTPS, reject URL credentials, query strings and fragments, and validate timeout bounds.
- Reject literal non-public IP addresses.
- Permit an operator to add an exact hostname through `YUKI_CLOUD_ALLOWED_HOSTS` (comma-separated) or the constructor's explicit `allowed_hosts` parameter.
- Keep credentials in environment variables; never put them in YAML or URLs.
- Add unit tests for trusted hosts, custom allowlisting, URL rejection and exact hostname matching.

## Operator guidance
Set `YUKI_CLOUD_ALLOWED_HOSTS` only to exact provider hosts that your organization has reviewed. Do not allowlist arbitrary user-supplied values or use wildcard suffix matching. Custom compatible gateways receive prompts and the configured API key; verify their ownership, logging, retention and privacy policy before use.

## Limits
This validates configured URL syntax and hostname policy, but is not a complete SSRF defense against compromised DNS or network routing. Production deployments should also enforce outbound egress rules at the host/container/network layer. Redirect behavior and TLS verification should remain default-secure; do not disable certificate checks. Tests are committed but must run in CI; no live network requests are made by these tests.
