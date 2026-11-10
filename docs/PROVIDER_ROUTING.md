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


## Exact-model routing in the specialist runtime

For OpenAI-compatible and local loopback Ollama adapters, the specialist worker now selects an exact configured model through the model capability registry before constructing the adapter. The selected model is pinned into that adapter instance and availability is checked before the handler is registered. Failure is terminal for that route; there is no model/provider fallback.

The current shared adapter contract proves text generation only. Vision, audio, video, tool calling, reasoning quality and context-window sizes remain unknown unless separately verified for the exact provider/model pair. Remote or ambiguous Ollama endpoints are not classified as local. Gemini CLI and hybrid remain on the explicit provider-level route until their exact model identity and construction can be pinned.

Model-level capability routing is currently available through `ProviderManager.create_routed_model_adapter()`; it is not a claim that every agent role or modality is supported.


The interactive CLI also constructs its initial OpenAI/Ollama adapter through exact-model routing. Switching to OpenAI is treated as an explicit cloud-provider selection at the CLI command boundary; programmatic callers must pass `allow_cloud=True` themselves. Switching to local Ollama uses the exact configured model identifier. Gemini CLI continues through its existing adapter path because the current registry does not yet pin a verifiable exact Gemini model.
