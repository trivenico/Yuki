# Phase 204 — capability-aware provider routing

- Added deterministic provider selection over the trusted provider registry.
- Requires declared task capabilities and explicit readiness evidence showing live verification, live verification level, and reachability.
- Prefers local providers; ties are resolved by provider ID for deterministic behavior.
- Cloud providers are excluded by default and require both a cloud-permitting task policy and explicit cloud opt-in.
- Unknown privacy classifications, unverified providers, unreachable providers, and capability mismatches are excluded.
- The router returns a structured, content-free decision and does not construct or contact providers.

## Safety and integration boundaries

- Readiness evidence must come from trusted application code and the existing live conformance/readiness workflow, never from a user prompt.
- This is a selection primitive, not an active health monitor. It does not probe providers or automatically retry another provider after failure.
- Callers must pass the selected ID to the registry resolver and preserve the same privacy authorization at resolution time.
- Routing is not yet integrated into every runtime, worker, or specialist agent path.
- Corrected the semantic-version regular expression in the Phase 203 registry so valid versions such as 1.0.0 are accepted.
