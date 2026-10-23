# Phase 61 — Compatible provider adapters and explicit routing

Yuki can use the following OpenAI-compatible Chat Completions endpoints through `providers.openai_compatible.OpenAICompatibleProvider`:

| Provider | Model environment variable | Credential environment variable |
|---|---|---|
| OpenAI | `YUKI_OPENAI_MODEL` | `OPENAI_API_KEY` |
| DeepSeek | `YUKI_DEEPSEEK_MODEL` | `DEEPSEEK_API_KEY` |
| Kimi / Moonshot | `YUKI_KIMI_MODEL` | `MOONSHOT_API_KEY` |
| MiniMax | `YUKI_MINIMAX_MODEL` | `MINIMAX_API_KEY` |
| OpenRouter | `YUKI_OPENROUTER_MODEL` | `OPENROUTER_API_KEY` |
| TokenRouter | `YUKI_TOKENROUTER_MODEL` | `TOKENROUTER_API_KEY` |
| Hugging Face Inference Router | `YUKI_HF_MODEL` | `HF_TOKEN` |
| Z.ai / GLM | `YUKI_ZAI_MODEL` | `ZAI_API_KEY` |

Choose the exact model ID documented by your provider. The adapter sends a single non-streaming chat-completions request, bounds prompt and response size, uses a finite timeout, rejects redirects, restricts HTTPS hostnames, and reads credentials only from environment variables. Configure keys in your process environment or secret manager; never commit them to YAML or source control.

## Select a provider

Set `provider` in the existing Yuki configuration to one of the names above. Set the corresponding model and credential environment variables, then start Yuki. The provider catalog reports whether the model and credential are configured; it does not call the provider or verify billing, model access, quotas, or remote availability.

The adapter supports only providers that expose a compatible `/chat/completions` API. Anthropic is intentionally still marked unimplemented because its native Messages API is not interchangeable with OpenAI Chat Completions. Do not configure Anthropic as an OpenAI-compatible provider unless using a separate, explicitly trusted compatibility gateway.

## Routing and privacy

The default provider remains `mock` unless configured otherwise. The existing hybrid provider routes to cloud only for explicitly allow-listed task names; it does not silently fall back from local to cloud. Do not send confidential project content to cloud providers unless your data policy permits it. Gateways such as OpenRouter and TokenRouter introduce an additional service in the data path.

## Hugging Face discovery

This phase adds the Hugging Face Inference Router as an inference adapter, not a live Hub crawler or auto-installer. Model discovery, license/security review, quantization/runtime checks and new-model notifications should be implemented as a separate scheduled discovery pipeline with source provenance and human approval before enabling any model.

## Validation

Mocked tests cover adapter construction for each supported provider, missing model configuration, missing credentials, manager selection, and the explicit Anthropic limitation. No real provider requests or paid inference are made by the test suite.
