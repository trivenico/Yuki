# Phase 68 — Provider capability registry

Yuki has a sanitized provider catalog. Phase 68 adds a stricter capability registry that can support future task routing without assuming that a configured provider or API key is operational.

## What it does

- Represents provider capabilities, readiness, privacy classification and configured model separately.
- Validates provider names and supported capability values.
- Selects only implemented, ready providers that satisfy every required capability.
- Excludes non-local providers by default; callers must explicitly opt into cloud/gateway candidates.
- Supports a caller-specified preference order, then deterministic alphabetical tie-breaking.
- Converts the existing sanitized catalog while ignoring descriptive hints that are not guaranteed adapter capabilities.
- Exposes `ProviderManager.routing_registry()` for future callers.

## Example

```python
from yuki_providers import ProviderManager

manager = ProviderManager(environ={})
registry = manager.routing_registry()
print(registry.list())

# A route can only be selected from entries whose adapter is implemented,
# ready, and whose privacy classification satisfies the requested policy.
try:
    selected = registry.route(required=("text",), preference=("ollama", "mock"))
    print(selected.provider)
except LookupError:
    print("No provider satisfies the current policy and readiness state.")
```

## Safety and limits

- This is a capability-selection primitive, not yet a full model router or inference runtime.
- It makes no network requests and does not execute a provider.
- Provider "ready" means the current catalog's local readiness checks passed, not that authentication or remote service health has been verified.
- Unknown capabilities are not inferred. Model-dependent features such as vision, tools, context window and structured output must come from verified model metadata or explicit adapter declarations before routing on them.
- Cloud routing requires explicit opt-in. A preference list is not authorization to bypass privacy policy.
- API keys are never returned by this registry.
