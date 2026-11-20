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


## CLI provider diagnostics

Run `yuki provider-status` to inspect the configured adapter without sending a prompt. The exit code is zero only when the adapter's own availability check establishes readiness. The JSON includes `availability_scope` so the result is not overstated:

- `server_and_model_verified` (or another adapter-defined scope): the adapter's own readiness check passed.
- `api_key_present_only`: the configured API key environment variable is non-empty; this does not verify credentials, network access, quota, or model access. Status is `configured_connection_unverified`.
- `executable_installed_only`: the Gemini CLI executable is present; account authentication is not tested. Status is `installed_authentication_unverified`.
- `unavailable`: the adapter's availability check failed.

A non-zero status for configured-but-unverified providers is intentional. Use a deliberate, low-risk test prompt with `yuki ask` or `yuki chat` to verify an actual request. Never paste API keys into prompts or YAML; provide them through the documented environment variable or an OS credential store.
