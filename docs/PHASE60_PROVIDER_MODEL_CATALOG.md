# Phase 60 — Provider and model catalog

The provider catalog is local-only metadata: it never contacts provider endpoints, lists live remote models, or sends prompts. It reports which adapter exists, the configured model identifier (if syntactically valid), whether an API credential appears configured, and what prerequisite is missing.

## Supported adapter status
- Implemented: mock, Ollama, Gemini CLI, hybrid local/cloud routing.
- Catalog-only/planned: direct OpenAI, Anthropic, DeepSeek, Kimi/Moonshot, MiniMax, OpenRouter, TokenRouter and Hugging Face inference.
- A credential or model name does not make a planned provider usable. Catalog entries stay `implemented: false` and `ready: false` until an adapter is implemented and tested.

## Configuration and privacy
Provider model IDs can be set with environment variables such as `YUKI_OLLAMA_MODEL`, `YUKI_CLOUD_MODEL`, `YUKI_OPENAI_MODEL`, `YUKI_DEEPSEEK_MODEL`, `YUKI_KIMI_MODEL`, `YUKI_MINIMAX_MODEL`, `YUKI_OPENROUTER_MODEL`, `YUKI_TOKENROUTER_MODEL` and `YUKI_HF_MODEL`. Secret values are never included in the catalog. The boolean `credential_configured` only indicates whether the expected environment variable is non-empty.

Gemini CLI readiness means only that the executable exists; it does not prove the user is signed in. Ollama readiness means a model name is configured, not that the Ollama server is running or that the model is installed. Those deeper checks require an explicit diagnostic action.

No API keys are written to YAML. Cloud routing remains opt-in. Model discovery from Hugging Face or gateways is not included in this phase; it needs a separately scoped, rate-limited, source-attributed discovery service.
