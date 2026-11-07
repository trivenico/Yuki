# Phase 198 — live provider conformance

Added `python -m tools.provider_live_conformance` to exercise the actual
configured provider instead of substituting a mock provider.

The command refuses mock configuration/provider instances and requires
`--confirm-live-request`. It performs a minimal chat request with no tools;
returned tool proposals cannot be executed. Optional native streaming sends a
second minimal prompt only when explicitly requested. Reports omit response
content, credentials, endpoints and exception messages.

Examples:

- `python -m tools.provider_live_conformance --confirm-live-request --json`
- `python -m tools.provider_live_conformance --confirm-live-request --include-streaming --json`

A result is `partial` if a capability was not exercised. It is `pass` only
when all listed checks ran and passed. The accompanying unit tests cover
report-state semantics only; they do not pretend to validate a live provider.
Live checks require an operator-configured provider, credentials or running
local runtime, and explicit authorization. They were not run in this GitHub
source-only development session.
