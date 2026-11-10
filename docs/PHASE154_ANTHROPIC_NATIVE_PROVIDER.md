# Phase 154 — Native Anthropic provider

## Provider support

Yuki now has a dedicated Anthropic Messages API adapter rather than routing Anthropic through an OpenAI-compatible shim.

- Endpoint: `https://api.anthropic.com/v1/messages`
- Authentication: `ANTHROPIC_API_KEY` environment variable
- Model: `YUKI_ANTHROPIC_MODEL` or `anthropic_model` configuration
- API version header: `anthropic-version: 2023-06-01`
- Redirects are rejected; only the exact Anthropic HTTPS origin is accepted.

## Event normalization

The adapter consumes Anthropic SSE events and maps text deltas to Yuki's normalized stream contract. Tool-use blocks and partial JSON argument deltas are reassembled and validated against advertised tool names and Yuki's canonical structured tool protocol.

The provider never executes tools. All tool proposals remain subject to `StructuredToolProtocol` and `GuardedToolRuntime`.

## Provider selection

```bash
export ANTHROPIC_API_KEY="..."
export YUKI_ANTHROPIC_MODEL="your-enabled-model-id"
```

Then select `anthropic` through the provider configuration or `ProviderManager`. No model identifier is assumed; the deployment supplies the model it is entitled to use.

## Resource boundaries

- 2 MB maximum stream size.
- 64 KiB maximum SSE line.
- 24,000 characters of visible streamed text.
- 8,000 characters of partial tool JSON per tool block.
- Five tool-use blocks maximum.
- Incomplete message streams and malformed/unadvertised tool proposals fail closed.

## Limitations

- The adapter is implemented against the Anthropic Messages API event contract; a live account/model smoke test still requires deployment credentials.
- Provider-specific token accounting and model-specific context limits remain separate work.
- Client disconnect cancellation is not yet propagated to the upstream provider.
