# Ollama Provider Streaming

\`OllamaProvider.stream(prompt)\` consumes the local \`/api/generate\` NDJSON
stream and yields each \`response\` delta. It requires the terminal \`done: true\`
event; truncated streams are failures. Individual event lines are limited to
1 MB and total generated text to 32,000 characters.

Closing the Python generator exits the response context and closes the HTTP
connection. This allows ConversationRuntime to propagate consumer cancellation
to the local transport. The adapter retains existing endpoint validation and
request timeout. Retries and resume are not implemented; partial output is
never treated as a completed assistant turn.
