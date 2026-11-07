# Phase 200 — runtime provider readiness integration

Wired provider readiness inspection into `ProviderRuntimeManager.readiness()`.
It inspects the active adapter instance, checks its availability when supported,
and reports structural capabilities without sending a prompt or changing the
active provider. A matching live conformance report is still required for
`live_verified`.

The provider factory now rejects `provider: mock` for runtime construction.
Runtime provider switching also rejects mock provider instances before
persisting or mutating active state. Existing unit tests should use pure
logic fixtures where appropriate; no mock LLM is treated as a real provider.

This is an integration at the shared runtime manager boundary, not yet a
universal guard on every custom provider-adapter callback in the application.
The provider factory still defaults to its existing OpenAI selection when
configuration omits a provider; deployments should explicitly set
`provider: ollama` for local-first operation. No cloud fallback is introduced.
