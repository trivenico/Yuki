# Phase 227 — Pinned HTTPS transport for cloud providers

## Transport guarantees

- Resolve the approved hostname immediately before each request.
- Reject empty, malformed, private, loopback, link-local, reserved and mixed public/non-public DNS answer sets.
- Select one validated public address and connect directly to that IP; do not perform a second DNS lookup during socket connection.
- Preserve TLS certificate and hostname verification against the original provider hostname.
- Disable ambient proxy environment configuration.
- Reject redirects so credentials and prompts cannot be forwarded to a different host.
- Apply the same opener to standard chat, structured tool-call and streaming requests.

## Remaining deployment controls

Application-level pinning is defense in depth, not a substitute for network egress policy. Production deployments should also restrict outbound traffic at the OS/container/network boundary, monitor DNS and destination changes, and use explicit provider endpoint allowlists. Network behavior can vary across operating systems and Python/OpenSSL versions and must be exercised in supported deployment environments.

## Validation

Regression tests cover public resolution, mixed/private answer rejection, empty DNS responses, direct pinned socket connection, TLS hostname preservation, proxy disabling and redirect-handler installation. Exact-head CI is required; no local test execution is claimed.
