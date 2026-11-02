# Conversation Runtime Adapter

\`automation.conversation_service.ConversationService\` is the application-facing
facade for CLI and desktop conversation clients.

It provides session creation, typed user/assistant/system messages, typed tool
lifecycle events, history pagination and session listing. Every operation
delegates authorization to \`AgentSessionStore\`.

## Safety and integration boundary

- This adapter persists events; it does not invoke a model or execute a tool.
- Tool events are audit/history records only, never instructions to replay.
- The caller must authenticate the actor and provide an authorization callback
  that checks the requested action and resource.
- Tool dispatch must independently authorize each call at execution time.
- Keep secrets and large attachments out of event payloads; store protected
  attachment references instead.
- Model output, tool output, repository files and retrieved content are
  untrusted inputs.
- Provider retries must use stable idempotency keys so a network retry does not
  duplicate a conversation event.

Run focused tests with:
\`python -m unittest tests.test_agent_sessions tests.test_conversation_service\`

The next integration phase should connect this facade to the existing provider
registry and CLI/desktop API, with streaming, cancellation and approval handling
implemented by separately tested components.
