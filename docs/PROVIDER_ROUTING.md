# Per-specialist provider routing

Yuki's local worker runtime can route each specialist role to an explicit provider preference list. This is a provider-level route; individual model IDs are selected from the provider configuration.

## Example

```python
import os

from automation.local_worker_runtime import build_local_worker_runtime
from yuki_providers import ProviderManager

manager = ProviderManager(
    config={"ollama_model": "qwen3"},
    environ=os.environ,
)

runtime = build_local_worker_runtime(
    "./yuki_workspace",
    provider_manager=manager,
    provider_preferences={
        "research": ("ollama",),
        "coding": ("openai", "gemini"),
        "testing": ("ollama",),
        "reporting": ("ollama", "openai"),
    },
    allow_cloud=True,
)
```

Set `OPENAI_API_KEY` and optionally `YUKI_OPENAI_MODEL` before using OpenAI. The default OpenAI model is `gpt-4.1-mini`. Ollama uses `ollama_model` or `YUKI_OLLAMA_MODEL`. Gemini requires the official `gemini` CLI; installation can be detected, but account authentication is not verified until a request is attempted.

## Privacy and failure behavior

- Cloud providers (OpenAI, Gemini and hybrid task-policy routes) require `allow_cloud=True`.
- Provider preference tuples are strict allowlists. An unavailable preferred provider does not silently fall back to an unlisted provider.
- With cloud permitted and no explicit preference order, the capability router still prefers local providers.
- Ollama is classified as local only for a valid loopback endpoint. Remote or ambiguous endpoints are excluded from automatic routing.
- The selected adapter's availability is checked before handlers are built. A failed check aborts construction; it does not silently switch providers.
- Hybrid providers call `generate_for(role, prompt)`. Only role names explicitly present in `YUKI_CLOUD_TASKS` can use the configured cloud adapter; all other roles stay local.
- Provider routing selects a provider adapter, not a guaranteed model capability. Yuki does not infer multimodal, tool-calling, or context-window support from provider names.

The older `provider` argument to `build_local_worker_runtime` remains available for callers that explicitly construct and pass an adapter themselves. That direct injection path bypasses catalog-based provider selection because the caller has already supplied the adapter.
