# Phase 148 — Persistent conversations and agent history

## Delivered

- Add `ConversationStore`, a SQLite-backed conversation/message store.
- Scope every read and write by the authenticated caller's stable `owner_id`; the store deliberately does not authenticate callers itself.
- Add optimistic conversation versions, per-message idempotency keys, bounded message sizes, pagination, archive support, and transactional append operations.
- Extend `AgentExecutionLoop.run` with bounded prior user/assistant history. System messages cannot be injected through persisted history.
- Add `ConversationService` to compose authenticated owner scope, persistent history, the bounded agent loop, and assistant response persistence.
- Sanitize model/guarded-operation failures into a stable user-facing response rather than persisting provider exception details.
- Add store, agent-history and service orchestration tests.

## Application composition

```python
store = ConversationStore(".yuki/conversations.sqlite3")
agent_loop = AgentExecutionLoop(
    ProviderManager(config).create_selected_agent_model(),
    structured_tool_protocol,
)
conversations = ConversationService(store, agent_loop)

conversation = conversations.create(owner_id=authenticated_user.id)
result = conversations.reply(
    owner_id=authenticated_user.id,
    conversation_id=conversation["conversation_id"],
    user_text="Hello Yuki",
    request_id=request_id,
    expected_version=0,
    tools=advertised_tool_schemas,
)
```

The web/API layer must derive `owner_id` from a verified authenticated principal, never from a caller-controlled request field. It must construct the `StructuredToolProtocol` using the application's authorization policy and approval system.

## Concurrency and deployment boundary

- SQLite writes are transactional and use optimistic versions and idempotency keys.
- `ConversationService` serializes calls within one process instance.
- Multi-process API deployments must add a database-backed generation lease before sharing one SQLite database between workers. Do not deploy multiple independent service instances against the same conversation database for generation until that lease is implemented.
- This phase is a framework-independent application service, not a deployed HTTP API or streaming transport.
- Provider streaming, browser UI, authentication middleware, file upload, cancellation endpoints and cross-process generation leases remain future work.

## Validation

Tests cover owner isolation, stale writers, idempotent replay, pagination, archive behavior, bounded history, prior transcript preservation, sanitized provider failure and end-to-end fake-model conversation turns.
