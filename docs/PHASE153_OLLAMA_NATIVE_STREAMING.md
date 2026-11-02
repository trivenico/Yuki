# Phase 153 — Ollama native streaming

## Transport

The Ollama provider now implements `generate_chat_stream` against `POST /api/chat` with `stream: true`. It consumes Ollama's newline-delimited JSON response, emits normalized `text_delta` events, and finishes with a provider-neutral response envelope.

## Tool proposals

Tool calls are read from Ollama message chunks and normalized to Yuki's canonical tool-call envelope. Tool names must have been advertised in the request, arguments must be JSON objects, and the result passes through `parse_tool_calls`. The adapter does not execute tools.

The final envelope is processed by the same AgentExecutionLoop and guarded tool protocol used by the OpenAI-compatible SSE adapter.

## Limits and failure behavior

- Local Ollama remains local; there is no cloud fallback.
- Redirects are rejected.
- Total NDJSON response: 2 MB.
- Individual line: 64 KiB.
- User-visible streamed text: 24,000 characters.
- Maximum tool calls: five.
- A final `done: true` event is mandatory.
- Invalid JSON, malformed tool calls, unadvertised tools, and incomplete streams fail closed.

## Integration

```python
provider = OllamaProvider(base_url="http://127.0.0.1:11434", model="your-installed-model")
adapter = ProviderAgentAdapter(provider)
loop = AgentExecutionLoop(
    adapter,
    guarded_tool_protocol,
    stream_model=adapter.stream_model,
)
```

The same authenticated conversation SSE endpoint can now stream local Ollama text when the application composes its agent loop with `adapter.stream_model`.

## Remaining work

- Native Anthropic Messages API streaming is still separate work.
- The Ollama adapter requires an installed and running Ollama model for a live smoke test.
- Client disconnect cancellation and provider abort remain later reliability work.
