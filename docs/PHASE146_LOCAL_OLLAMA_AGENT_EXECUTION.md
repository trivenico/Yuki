# Phase 146 — Local Ollama agent execution

## Delivered

- Add `OllamaProvider.generate_chat(messages, tools, tool_choice)` with role-preserving transcripts.
- Use Ollama's local `/api/chat` endpoint; do not silently route local prompts to cloud.
- Normalize returned function-call proposals into Yuki's structured tool protocol.
- Validate message count, transcript size, tool count, tool names, schemas, tool-call count, and response size.
- Reject unadvertised tool names, malformed arguments, and tool-choice contract violations.
- Disable HTTP redirects for chat requests.
- Add deterministic transport-mocked tests; no live Ollama server or network access is required.

## Local configuration

Set `provider: ollama` and configure `ollama_model` in the existing Yuki configuration. The model must be installed in the local Ollama instance. API requests use the configured local endpoint.

The bounded agent loop can now be constructed with `ProviderAgentAdapter(OllamaProvider(...))`. Tool proposals still pass through `StructuredToolProtocol` and `GuardedToolRuntime`; Ollama never executes Yuki tools itself.

## Scope and limitations

- This change implements the chat contract for Ollama and OpenAI-compatible providers.
- Gemini CLI and hybrid routing still expose text generation only and need an explicit chat/tool-call contract before they can be used with this agent loop.
- No web UI, streaming transport, live-provider smoke test, or automatic task enqueue is introduced in this phase.
- Local Ollama endpoint access remains a deployment trust decision; the default is loopback HTTP, and non-loopback endpoints require HTTPS.

## Validation

Tests cover role preservation, tool proposal normalization, invalid transcript rejection, unadvertised tool rejection, malformed arguments, and tool-choice enforcement.
