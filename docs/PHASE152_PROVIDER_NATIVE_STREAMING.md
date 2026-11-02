# Phase 152 — OpenAI-compatible native streaming

## Supported provider transport

The OpenAI-compatible provider now implements `generate_chat_stream` using Server-Sent Events from `/chat/completions`. It works with endpoints that implement the compatible streaming protocol, including the configured OpenAI, DeepSeek, OpenRouter and other allow-listed compatible endpoints.

This phase does not claim native Anthropic Messages API support. Anthropic requires a separate event parser and request/response adapter.

## Normalized provider events

The provider yields:

- `{"type": "text_delta", "text": "..."}`
- `{"type": "response", "response": {"text": "...", "tool_calls": {"tool_calls": [...]}, "finish_reason": "..."}}`

Tool-call fragments are accumulated by index, bounded, parsed as JSON objects, checked against the advertised tool names, and validated with Yuki's canonical structured tool protocol. The provider never executes tools.

## Agent-loop wiring

```python
provider = create_provider(config)
adapter = ProviderAgentAdapter(provider)
loop = AgentExecutionLoop(
    adapter,
    guarded_tool_protocol,
    stream_model=adapter.stream_model,
)
```

The existing authenticated SSE conversation endpoint can now emit provider text deltas when this wiring is used. If the selected provider has no native stream method, the streaming adapter fails closed rather than pretending that a buffered response is incremental.

## Security and resource limits

- HTTPS and exact trusted-host validation remain in force.
- Redirects are rejected; API keys are not forwarded to redirected hosts.
- API keys are read from environment variables only.
- Response stream is capped at 2 MB, individual SSE lines at 64 KiB, visible text at 24,000 characters, tool arguments at 8,000 characters, and tool calls at five.
- Incomplete streams, malformed events, unadvertised tools and invalid JSON arguments fail closed.
- Tool proposals continue through `StructuredToolProtocol` and `GuardedToolRuntime`; the adapter only normalizes provider output.

## Remaining provider work

- Implement a separate Anthropic Messages API adapter and its content-block/tool-use event mapping.
- Implement Ollama NDJSON streaming and normalize its tool-call format.
- Add provider-specific retry policy only for failures known to occur before generation starts; never replay a partially emitted answer automatically.
- Add integration tests against local mock HTTP servers and provider contract fixtures.
