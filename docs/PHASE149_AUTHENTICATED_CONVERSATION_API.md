# Phase 149 — Authenticated conversation HTTP API

## Delivered

- Add `YukiConversationAPI`, a dependency-free ASGI 3 HTTP application.
- Add bearer-session login and logout using the existing `BuiltInAuthService`.
- Add authenticated conversation list/create, message history and reply endpoints.
- Derive conversation owner scope exclusively from the resolved session principal. Request JSON cannot select or override owner identity.
- Keep tool definitions behind a trusted server-side `tools_for_user` callback; clients cannot submit arbitrary tool schemas.
- Bound request bodies to 24 KB, validate payload shapes and pagination, and return generic errors without provider exceptions or SQL details.
- Return `Cache-Control: no-store` and `X-Content-Type-Options: nosniff` on JSON responses.
- Add ASGI-level tests for login, logout, authenticated multi-turn chat, forged owner fields, invalid credentials, unknown conversations and oversized bodies.

## Composition

The embedding application constructs the provider, guarded tool protocol, conversation store and services. It then mounts:

```python
auth = BuiltInAuthService(".yuki/auth.sqlite3")
store = ConversationStore(".yuki/conversations.sqlite3")
provider_model = ProviderManager(config).create_selected_agent_model()
protocol = StructuredToolProtocol(GuardedToolRuntime(approved_tools, authorization_policy))
agent_loop = AgentExecutionLoop(provider_model, protocol)
conversation_service = ConversationService(store, agent_loop)
app = YukiConversationAPI(auth, conversation_service, tools_for_user=server_tool_schemas)
```

Bootstrap the first administrator out of band with `auth.bootstrap_admin(...)`. Never expose that operation as public signup.

## HTTP contract

- `POST /v1/auth/login` — username/password; returns opaque bearer token and expiry.
- `POST /v1/auth/logout` — revoke current session.
- `GET /v1/conversations` — list current user's conversations.
- `POST /v1/conversations` — create a conversation.
- `GET /v1/conversations/{id}/messages` — retrieve message history; optional `after_sequence`.
- `POST /v1/conversations/{id}/messages` — submit `content`, `request_id`, and `expected_version`.

## Production boundary

This is an HTTP ASGI application, not a production deployment recipe. Mount it behind TLS and a trusted reverse proxy; configure origin policy, network exposure, secrets, persistent volume permissions, backups and operational monitoring at the deployment layer. Account creation and administrator bootstrap remain out of band. The synchronous conversation service currently serializes generation within one process; multi-worker deployment requires a database-backed generation lease. Streaming, cancellation endpoints, request rate limiting and cross-process leases are not claimed by this phase.
