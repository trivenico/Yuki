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


## Model control

- `GET /v1/models` — authenticated, sanitized provider/model readiness catalog.
- `POST /v1/models/select` — authenticated provider selection; JSON
  `{"provider":"ollama"}`.

The catalog never returns credential values. Provider selection is explicit.
The selected provider becomes a strict routing preference; Yuki does not silently
switch to a different provider when it is unavailable or blocked by privacy
policy. Cloud providers remain excluded unless the calling workflow explicitly
opts in. The current local conversation API opts out of cloud routing. The desktop model selector only permits providers classified as `local_runtime`; cloud and non-loopback endpoints are not selectable through this local UI.


## Personal memory API

Memory is stored separately from conversation events in `memories.sqlite3`.
The HTTP API currently exposes personal memory only; the scope is derived from
the authenticated user and cannot be supplied by the client.

- `GET /v1/memories` — list personal memories; optional `category`.
- `GET /v1/memories?q=term` — search personal memory text.
- `POST /v1/memories` — create a memory with `category`, `content`, and optional `metadata`.
- `DELETE /v1/memories/{memory_id}` — delete one personal memory.

Supported categories: preference, fact, decision, procedure, project, skill, note.
Project-scoped storage exists at the store layer, but project memory is not exposed
over HTTP until project membership and authorization checks are implemented.
Memory is not automatically injected into model prompts in this phase.


## Memory proposal review

Suggested memories remain outside the searchable memory store until the
authenticated owner approves them.

- `GET /v1/memory-proposals` — list pending proposals; optional `?status=all|approved|rejected`.
- `POST /v1/memory-proposals` — submit `{"category":"preference","content":"..."}`.
- `POST /v1/memory-proposals/{proposal_id}/approve` — approve and persist.
- `POST /v1/memory-proposals/{proposal_id}/reject` — reject without persisting.

The owner and personal scope are derived from the authenticated session.
This is a review workflow, not an automatic conversation extractor; Yuki does
not yet infer or submit suggestions from conversation content.


## Memory suggestion preview

- `POST /v1/memory-suggestions/preview` accepts `{"text":"..."}` and returns
  conservative rule-based candidates, source evidence, confidence, duplicate
  references and possible-conflict references.
- The endpoint is authenticated and derives the personal memory scope from the
  authenticated principal.
- Preview is strictly non-persisting. It does not create a proposal or memory.
  A user must separately submit a proposal and approve it.
- Extraction is heuristic, not semantic model reasoning; candidates require
  human interpretation and approval.
