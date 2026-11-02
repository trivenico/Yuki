# Streaming Provider Manager Integration

\`ConversationRuntime.respond_stream_with_manager\` authorizes the actor/session
before provider discovery, then asks the provider manager for a text-capable
adapter using an explicit \`allow_cloud\` flag. Cloud routing defaults to false.
The selected adapter must expose \`stream(prompt)\` and \`is_available()\`.

Consumer closure is treated as interruption: the underlying provider iterator
is closed and a \`generation.interrupted\` audit event is recorded. Partial
assistant output is not persisted. This API is a Python iterator boundary only;
HTTP/SSE endpoint wiring and browser disconnect integration are not included.
