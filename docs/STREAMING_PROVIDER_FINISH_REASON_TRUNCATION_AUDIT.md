# Streaming completion semantics and truncated-output audit

## Scope

Source review at PR #1346 head `338f19fc45918adc11534db9cd00b287564aa46d`. Reviewed:

- `providers/openai_compatible.py` — `generate_chat_stream()`
- `providers/agent_adapter.py` — streaming adapter
- `core/agent_execution_loop.py` — stream consumption and tool dispatch
- `core/structured_tool_protocol.py` — final tool-envelope validation

This is a source-level review. No tests, live provider calls, or stream fault-injection were executed.

## Finding: non-success finish reasons are treated as successful model turns

**Severity: Moderate (P2) — truncated answers and potentially premature tool execution**

### Evidence

1. `OpenAICompatibleProvider.generate_chat_stream()` records `choice["finish_reason"]` when present and includes it in the final `response` event.
2. The adapter accepts a stream once it has either observed the SSE `[DONE]` marker or any non-null `finish_reason`. It does not restrict finish reasons to successful terminal states.
3. `AgentExecutionLoop.run()` validates the response envelope's keys and text/tool-call shapes, but does not inspect `finish_reason`.
4. When no tool calls are parsed, any non-empty text is returned with status `completed`, regardless of whether the provider ended with `length` or `content_filter`.
5. When tool calls are present, the loop proceeds to `StructuredToolProtocol.dispatch()` after shape/protocol validation, also without checking whether the provider's finish reason indicates truncation or filtering.
6. JSON/protocol validation proves that a tool-call envelope is syntactically valid and bounded; it does not prove that the model completed the intended generation. A truncated argument fragment can still form a valid but semantically incomplete JSON object.

### Impact

- A response cut off by the provider's output-token limit can be surfaced as a completed answer instead of an incomplete/limit result.
- A response ending due to content filtering can be presented as a normal final answer.
- If a tool-call argument prefix happens to be valid JSON before truncation, the agent loop may dispatch that partial proposal. Guarded authorization still applies, but the requested operation may differ from the complete operation the model would otherwise have proposed.
- Downstream callers may persist or act on a response whose provider completion status was not successful.

This is not evidence of tool execution from raw streaming fragments. The current design waits for the adapter's final response event and validates the structured envelope before dispatch. The defect is that terminal status is discarded at the final-response boundary.

### Recommended remediation

1. Define a provider-neutral terminal-reason contract. Explicitly allow successful completion reasons (for example, `stop` and `tool_calls`) and classify truncation, content filtering, and unknown reasons as non-success or indeterminate.
2. Before returning a completed answer or dispatching any tool, have `AgentExecutionLoop` validate `finish_reason`. Reject `length` and `content_filter`; reject unknown non-null values unless a provider-specific adapter maps them deliberately.
3. For tool-call responses, require the expected tool-call terminal reason where the provider contract defines one. Never dispatch a tool call from a response marked truncated or filtered.
4. Decide and document the `[DONE]`/terminal-reason contract per provider. Do not treat a bare `[DONE]` as proof of successful semantic completion if the provider requires a finish reason.
5. Apply equivalent finish-reason handling to non-streaming `generate_chat()`, which also returns the provider's finish reason for the agent loop to interpret.
6. Add deterministic tests for: `stop`; `tool_calls`; `length` with ordinary text; `length` with a valid-but-incomplete tool argument object; `content_filter`; unknown finish reason; missing finish reason; disconnect before terminal event; and `[DONE]` without a terminal reason. Assert that no tool dispatch occurs for every non-success/indeterminate case.

### Existing safeguards

- Text and tool-call deltas are buffered; tool execution does not occur on each incoming delta.
- The adapter bounds stream bytes, line size, text size, and tool argument size.
- Tool-call names, IDs, arguments, and the complete envelope are validated.
- The structured protocol parses the complete batch before invoking guarded tools.
- Cancellation and execution-duration checks run while consuming the stream and again before dispatch.

These safeguards reduce exposure but do not replace semantic completion validation.

## Audit limitations

No tests or CI workflows were executed. No provider-specific behavior was live-verified. The finding is based on the inspected implementation and should be closed with deterministic stream fixtures and explicit per-provider finish-reason contracts.
