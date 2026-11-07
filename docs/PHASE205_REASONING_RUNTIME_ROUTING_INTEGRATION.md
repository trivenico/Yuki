# Phase 205 — owner-scoped reasoning runtime routing integration

- Added optional owner-scoped provider registry and readiness resolvers to the reasoning runtime factory.
- When configured, provider selection passes through ProviderRouter and ProviderRegistry; the legacy adapter resolver is not invoked.
- Requires the chat capability and live-verified, reachable readiness evidence before resolving a provider.
- Local-only is the default. Cloud selection requires the existing owner-scoped opt-in callback.
- Resolver pairing and return types are validated. Existing adapter-based deployments remain compatible.

## Boundaries

- Readiness evidence is supplied by trusted application code; this integration does not probe, infer, retry, or silently fall back.
- Integration currently covers the owner-scoped reasoning runtime only, not every worker, scheduler, or specialist agent.
- Trace identity callbacks remain caller-owned and must match selected provider configuration.
- No live provider request is made by routing.
