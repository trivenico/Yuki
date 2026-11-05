# Phase 201 — provider routing consistency

## Changes

- Centralize a defense-in-depth predicate for explicitly marked or conventionally named test/mock providers.
- Reject mock implementations both when selecting a provider and when restoring persisted selection.
- Before readiness is reported, construct the configured provider implementation and compare its concrete type with the active adapter. A mismatch fails closed instead of attributing the active adapter's capabilities or availability to another provider.
- Add regression tests for marked mock rejection and active/configuration mismatch.

## Boundaries

- Provider construction is required to remain side-effect-free: constructors must not send prompts, execute tools, or perform network requests.
- Concrete type matching is intentionally conservative. Decorators, proxies, hot reload, and dynamically wrapped providers may need an explicit stable provider identity contract in a later phase.
- This audit covers the shared ProviderRuntimeManager boundary. It does not prove every custom callback, worker, or third-party integration in the repository routes through this manager.
- Mock detection is defense in depth, not a substitute for trusted adapter registration and code review.
