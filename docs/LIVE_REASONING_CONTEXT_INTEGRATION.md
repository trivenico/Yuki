# Live conversation reasoning-context integration

The authenticated conversation service can accept an optional `ReasoningContextBuilder` through `build_conversation_runtime`.

When configured, the service:
- derives the memory actor exclusively from its authenticated `owner_id`;
- uses a server-configured agent ID (default `yuki`), never a request field;
- retrieves verified evidence by default, with shared-memory visibility still controlled by the underlying memory authorization callback;
- serializes preferences and retrieved evidence into a bounded, explicitly untrusted user-role context message;
- keeps the actual system policy in the system role and preserves the guarded tool protocol as the only tool authorization/execution path;
- degrades to no retrieved context if memory retrieval fails. It never retries with a broader actor or agent scope.

The execution loop validates the additional context at 32,000 characters. The conversation integration currently uses an 8,000-character serialized cap. These are character limits, not tokenizer-exact token budgets. A later provider adapter change must reserve output tokens and use the selected model's tokenizer or a conservative model-specific estimator.

## Deployment status

This is an opt-in runtime integration seam. The default browser deployment does not yet construct or inject an `AgentMemoryStore` / `UnifiedKnowledgeAPI` / `ReasoningContextBuilder`; therefore the existing web chat remains unchanged until a deployment supplies a memory store with a real owner-authorization callback. Do not replace that callback with unconditional authorization.
