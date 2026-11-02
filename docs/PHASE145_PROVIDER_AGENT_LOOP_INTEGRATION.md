# Phase 145 — Provider and bounded agent-loop integration

## Delivered

- Add `OpenAICompatibleProvider.generate_chat`, accepting a bounded, role-preserving message transcript instead of flattening it into one prompt.
- Normalize provider function-call proposals into Yuki's canonical structured-tool envelope.
- Add `ProviderAgentAdapter`, which exposes the explicit provider chat contract as the callable expected by `AgentExecutionLoop`.
- Keep tool execution outside provider adapters. Proposed calls are still validated and dispatched only by `StructuredToolProtocol` and `GuardedToolRuntime`.
- Add provider contract tests and an end-to-end fake-provider → agent loop → guarded tool → provider round-trip test.

## Integration

```python
provider = create_provider(config)
model = ProviderAgentAdapter(provider)
loop = AgentExecutionLoop(model, structured_tool_protocol)
result = loop.run(user_text, tools=advertised_tool_schemas)
```

The configured provider must implement `generate_chat(messages, tools, tool_choice)`. This phase adds that method to the OpenAI-compatible adapter. Other providers can opt in by implementing the same explicit contract.

## Safety and limits

- Message roles are preserved; no lossy concatenation is used.
- Transcript length, message count, tool count, schema size and provider response size are bounded.
- Redirects remain disabled for cloud requests.
- Provider output is a proposal only; authorization and execution remain in the guarded runtime.
- No credentials or provider response bodies are written to task events.
- This does not yet connect a live UI request, automatic task enqueue, task journal lease lifecycle, or approval UI into one deployed application flow.
- No live provider request is made by tests; CI and configured credentials are required for a live smoke test.

## Validation

Tests cover adapter contract validation, role preservation, tool-call normalization, malformed transcript rejection, and the complete model/tool/model round trip with a fake provider.
