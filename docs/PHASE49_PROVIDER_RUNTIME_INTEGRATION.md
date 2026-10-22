# Phase 49 — Provider registry/runtime integration

## Gap
The default branch has two different sources of truth. `providers.factory.create_provider` constructs mock, Ollama and hybrid adapters, while `yuki_providers.py` reports only mock as implemented and only creates subclasses of `BaseProvider`. Existing Ollama/hybrid adapters satisfy the callable provider contract without inheriting that base class.

## Changes
- ProviderManager now constructs built-in adapters through `providers.create_provider`.
- Registry marks mock, Ollama and hybrid as constructible; planned cloud providers remain unavailable on this branch.
- Provider contract validation is structural: callable `generate(prompt)` and `is_available()`.
- Selection validates construction before updating the selected provider, preserving the previous selection on configuration failures.
- Environment injection supports deterministic tests without mutating process environment.

## Use
```python
from core.config import load_config
from yuki_providers import ProviderManager

manager = ProviderManager(load_config())
print(manager.list_providers())
print(manager.select("ollama"))
provider = manager.create_selected_adapter()
if provider.is_available():
    print(provider.generate("Say hello briefly"))
```

The final two calls contact the configured model and should only be run when that behavior is intended. Constructing an adapter is not proof of server availability or successful inference.

## Limits
This phase does not integrate the task router, add cloud-provider adapters, enable implicit fallback, or merge any other open PR. Unit tests are added but must pass in CI before merging. No live model request is made by tests.
