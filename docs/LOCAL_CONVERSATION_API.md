# Yuki Local Conversation API

The standard-library HTTP server binds only to loopback. It is not internet-facing.
Remote deployment requires a TLS reverse proxy, network controls, rate limiting,
and a separate production review.

Before first startup set YUKI_BOOTSTRAP_ADMIN and YUKI_BOOTSTRAP_PASSWORD.
The existing BuiltInAuthService creates the initial administrator once. There
is no public signup endpoint.

Call api.conversation_server.serve(provider_manager) from Python after
constructing the existing Yuki ProviderManager.

Routes:
- GET /health
- POST /v1/auth/login (JSON username/password)
- POST /v1/auth/logout (Bearer token)
- GET /v1/me
- GET /v1/conversations
- POST /v1/conversations (optional Idempotency-Key)
- GET /v1/conversations/{id}/events (X-After-Sequence pagination)
- POST /v1/conversations/{id}/messages (SSE; JSON content; Idempotency-Key)

Cloud routing is disabled. SSE client disconnect closes the conversation
runtime iterator. Remaining pre-production work includes rate limiting,
TLS/reverse-proxy integration, operational telemetry, multi-client load tests,
and security review.
