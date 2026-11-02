# Provider-backed Conversation Runtime

\`automation.conversation_runtime.ConversationRuntime\` connects the durable
conversation service to Yuki's existing provider adapter contract.

## Flow

1. Caller authenticates the actor and selects a provider under model/cloud policy.
2. Runtime appends the user message to the actor-authorized session.
3. Runtime builds a bounded prompt from recent user/assistant/system events.
4. Runtime checks the selected provider's availability and calls generate().
5. Runtime persists the assistant response, or a minimal generation-failed audit event.

## Important limitations

- Text-only synchronous generation; no token streaming or cancellation.
- No tool execution, approval workflow, autonomous loop or automatic provider fallback.
- Provider selection and cloud-consent policy are owned by the caller.
- A model request can be repeated if the caller retries after an ambiguous network failure. The event idempotency key prevents duplicate user/assistant event admission, but does not guarantee exactly-once model inference.
- The prompt is bounded by event count and character count; production context-window budgeting and summarization are future work.
- The runtime must be placed behind the authenticated CLI/desktop/API boundary before exposure.

Tool execution remains a separate capability-gated workflow. Never infer permission
to execute a tool from a conversation event or model-generated text.

Run focused tests:
\`python -m unittest tests.test_agent_sessions tests.test_conversation_service tests.test_conversation_runtime\`
