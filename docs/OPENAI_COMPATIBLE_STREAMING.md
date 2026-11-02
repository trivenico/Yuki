# OpenAI-Compatible Streaming

\`OpenAICompatibleProvider.stream(prompt)\` requests Chat Completions with
\`stream: true\` and yields SSE \`choices[0].delta.content\` strings. It ignores
SSE keepalives, role-only chunks and usage-only events, and requires the
terminal \`data: [DONE]\` marker. Redirects remain disabled so credentials and
prompts cannot be forwarded to a different host.

Each event line is limited to 1 MB and total content to 32,000 characters.
Closing the generator closes the response context. API credentials are read
only from the configured environment variable. Provider-specific event
extensions beyond standard Chat Completions content deltas are rejected.
