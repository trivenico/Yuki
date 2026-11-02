# Streaming Conversation Runtime

\`ConversationRuntime.respond_stream(...)\` accepts a provider implementing
\`is_available()\` and \`stream(prompt)\`, yielding text chunks. Each chunk emits
a provider-neutral delta event. Clean completion appends exactly one assistant
message and emits completed.

Cancellation uses a caller-supplied \`threading.Event\`. It emits interrupted,
records a generation.interrupted audit event, and does not persist partial
assistant content. Provider failures are audited and raised as
ConversationGenerationError. The iterator is closed when supported.

This layer does not itself provide HTTP/SSE transport, browser disconnect
detection, provider-specific streaming adapters, tool calls, approvals,
automatic retries, or token counting. A production transport must implement
bounded timeouts and close the provider iterator on disconnect. Callers must
authenticate actors and apply cloud-consent policy before provider selection.
