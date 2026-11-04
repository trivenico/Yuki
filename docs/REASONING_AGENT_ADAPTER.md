# Reasoning Agent Adapter

`providers.reasoning_agent_adapter.ReasoningAgentAdapter` connects the bounded
memory context builder to Yuki's existing structured provider adapter.

## Flow

1. Validate incoming role/content messages using the provider adapter contract.
2. Retrieve authorized evidence for the configured actor and specialist agent.
3. Keep Yuki's configured policy and owner preferences in the system role.
4. Serialize retrieved records as explicitly untrusted JSON data in the latest
   user message. Unverified records are excluded unless explicitly enabled.
5. Forward the original tool definitions and tool-choice policy unchanged to
   `ProviderAgentAdapter`. This wrapper never executes proposed tools.

## Security boundary

Memory remains untrusted even when verified. Retrieved content cannot grant
permissions, change system policy, or directly invoke tools. Tool execution
remains exclusively behind `StructuredToolProtocol` and
`GuardedToolRuntime`.

The wrapper does not itself enforce provider-side role isolation against a
malicious provider implementation; the configured provider must honor the
structured messages contract. It also does not provide tokenizer-aware context
accounting; the existing character bounds remain an outer guard.

## Usage

Construct the wrapper only after the application's authorization/configuration
layer has selected the actor, agent, policy, and provider. Do not accept those
security-sensitive values from an untrusted task payload.
