# Phase 203 — trusted provider registry

- Added a thread-safe registry for trusted, versioned provider adapter factories.
- Registration requires a constrained provider ID, semantic version, explicit privacy class, and declared capabilities.
- Resolution checks capability requirements before constructing an adapter, rejects mock providers, validates the constructed adapter type, and verifies that runtime privacy metadata matches the registration.
- Cloud providers still require explicit opt-in through the central gateway.
- Registry descriptions expose metadata only; factories and credentials are never returned.

## Scope and limitations

- Registration is an application-code trust boundary. It is not a plugin sandbox or a way to safely load arbitrary third-party Python.
- Capabilities are trusted declarations, not live behavioral certification. Use the existing provider conformance and live verification workflow before production routing.
- The registry is an additive primitive and is not yet wired into every provider selection, worker, scheduler, or specialist-agent path.
- No provider is automatically discovered, contacted, retried, or substituted. No live provider request is made.
- No mock provider is used as a production provider.
