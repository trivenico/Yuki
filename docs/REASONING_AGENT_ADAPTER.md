# Reasoning Agent Adapter

`providers.reasoning_agent_adapter.ReasoningAgentAdapter` connects the bounded
memory context builder to Yuki's existing structured provider adapter.

## Flow

1. Validate incoming role/content messages using the provider adapter contract.
2. Retrieve authorized evidence for the configured actor and specialist agent.
3. Keep only server-configured Yuki policy in the system role.
4. Serialize owner preferences and retrieved records as explicitly untrusted
   JSON reference data in the latest user message. Unverified records are
   excluded unless explicitly enabled.
5. Forward the original tool definitions and tool-choice policy unchanged to
   `ProviderAgentAdapter`. This wrapper never executes proposed tools.

## Security boundary

Memory remains untrusted even when verified. Owner preferences are also
reference data, not policy: they cannot grant permissions or override the
server-configured system policy. Retrieved content cannot directly invoke
tools. Tool execution
remains exclusively behind `StructuredToolProtocol` and
`GuardedToolRuntime`.

The wrapper does not itself enforce provider-side role isolation against a
malicious provider implementation; the configured provider must honor the
structured messages contract. It also does not provide tokenizer-aware context
accounting; the existing character bounds remain an outer guard.

## Usage

Use `providers.reasoning_runtime.create_owner_scoped_agent_loop_factory` to
construct a fresh reasoning adapter and bounded execution loop for each
conversation request. `ConversationService` supplies the authenticated owner ID
only after conversation ownership and idempotency checks. The provider and
guarded-tool factories remain application-owned callbacks. Shared memory is
disabled by default; enabling it still requires the memory store's separate
`agent_memory:share` authorization. Do not accept actor IDs, provider selection,
agent IDs, or policy from an untrusted task payload.
