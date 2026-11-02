# Phase 147 — Selected-provider agent composition

## Delivered

- Add `ProviderManager.create_selected_agent_model()` as the application composition seam between configured provider selection and the bounded agent loop.
- Return `ProviderAgentAdapter` only when the selected provider explicitly implements `generate_chat(messages, tools, tool_choice)`.
- Fail closed for text-only integrations (currently Gemini CLI and hybrid routing) instead of flattening transcripts or silently rerouting agent/tool requests.
- Add registry-level tests for OpenAI-compatible and Ollama agent-model construction and for text-only hybrid rejection.
- Correct Ollama malformed-tool-argument error classification and assert the specific failure in its regression test.

## Application wiring

```python
provider_manager = ProviderManager(config, environ=environ)
agent_model = provider_manager.create_selected_agent_model()
agent_loop = AgentExecutionLoop(agent_model, structured_tool_protocol)
result = agent_loop.run(user_text, tools=advertised_tool_schemas)
```

The application still owns construction of the guarded tool protocol and its policy. This method does not grant permissions, check user approval, dispatch tools, or create a task.

## Provider support matrix

| Provider | Text generation | Structured agent chat |
|---|---:|---:|
| OpenAI-compatible | Yes | Yes |
| Ollama | Yes | Yes |
| Gemini CLI | Yes | Not yet |
| Hybrid | Yes | Not yet |

Hybrid is intentionally rejected for agent execution until task routing can preserve role transcripts and tool-call semantics end-to-end. This avoids unexpected privacy or routing changes.

## Validation

Deterministic tests cover provider manager composition, adapter construction, fail-closed text-only providers, and malformed Ollama tool arguments. CI must pass on the exact PR head before merge.
