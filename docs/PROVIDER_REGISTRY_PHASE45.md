# Phase 45 — Provider registry/runtime alignment

## Problem
`yuki_providers.py` only exposed `mock` as implemented, while `providers.factory.create_provider` supported `mock`, `ollama`, and `hybrid`. This made capability reporting diverge from runtime construction. The registry also required adapters to subclass `BaseProvider`, while existing Ollama/hybrid adapters use a compatible structural interface instead of inheriting that class.

## Fix
- `ProviderManager` now constructs supported providers through `providers.create_provider`, sharing configuration behavior with the runtime factory.
- Registry marks `mock`, `ollama`, and `hybrid` as implemented, while planned OpenAI/Anthropic/Gemini entries remain unavailable on this branch.
- Adapter validation checks callable `generate(prompt)` and `is_available()` methods instead of nominal inheritance.
- Selection constructs the candidate before changing current selection, so invalid configuration leaves the current selection unchanged.
- Configuration can be supplied through `ProviderManager(config, environ=...)`; provider credentials are not read from YAML by this manager.

## Usage

```python
manager = ProviderManager({"ollama_model": "your-installed-model"})
print(manager.list_providers())
print(manager.select("ollama"))
provider = manager.create_selected_adapter()
```

`implemented` means adapter construction is supported. It does not promise the server is running, a model is installed, cloud credentials are valid, or the request will succeed. Use `provider.is_available()` for runtime readiness checks.

## Validation
Run `python -m unittest tests.test_yuki_providers tests.test_provider_factory -v` and the full suite. These tests were added but have not been executed in this GitHub editing session. This phase does not merge provider-routing, Gemini CLI, Hugging Face or evaluation PRs; those remain separately reviewed branches.
