# Authenticated Memory API

## Endpoints

All routes below require a valid Yuki bearer session. Ownership is always derived from the server-resolved `AuthenticatedUser.user_id`; request bodies cannot choose an owner.

- `GET /v1/memory/consent` — read whether memory is enabled (disabled by default).
- `PUT /v1/memory/consent` — explicitly enable or disable memory with `{"enabled": true|false}`.
- `GET /v1/memory` — list the authenticated user's memories.
- `POST /v1/memory` — create an approved memory (`text`, optional `category`).
- `GET /v1/memory/search?q=...` — lexical retrieval with optional `limit` and `category`.
- `PATCH /v1/memory/{id}` — edit the authenticated user's memory.
- `DELETE /v1/memory/{id}` — delete only that user's memory.
- `GET /v1/memory/export` — export the currently consent-visible memory set.

## Consent semantics

Memory is disabled by default. Creating, listing, searching, and editing require opt-in; disabled reads return an empty list. Disabling consent stops future reads/writes but does not silently destroy existing records. Explicit deletion remains available while disabled. Export reports consent state and only returns memories when consent is enabled.

## Security model

- The ASGI API authenticates the bearer token through `BuiltInAuthService` before routing memory requests.
- The API passes its immutable authenticated user object to `UserMemoryService`; no client-supplied actor ID is accepted.
- `ScopedMemory` hashes the verified user ID into a per-user local namespace.
- Each account's consent state is stored separately in SQLite.
- Retrieved memory is data, not policy or tool authorization. This PR does not inject memories into prompts or automatically store conversation turns.
- The current local JSON store is a single-process storage primitive, not a distributed multi-worker transactional database. Production multi-worker deployments need a transactional tenant-scoped backend and encrypted backups.

## Runtime wiring

`build_conversation_runtime` accepts an optional `UserMemoryService` and passes it to the authenticated API. Deployment configuration must construct it with explicit storage paths. If no service is injected, memory endpoints return `503 memory_unavailable`; chat continues to work.

## Next steps

1. Add deployment configuration and lifecycle/backup handling for memory storage.
2. Add audit records for consent changes and destructive actions.
3. Add rate limits and pagination/cursors for list/search/export.
4. Add prompt-context retrieval only after an explicit product decision, with bounded tokens and untrusted-context delimiters.
5. Add cross-process concurrency and tenant-isolation tests before multi-worker production rollout.

## Contextual memory in conversation reasoning

When a `UserMemoryService` is injected into `build_conversation_runtime`, the runtime constructs a `MemoryContextBuilder` and injects it into `ConversationService`.

For each new turn:

1. The conversation API authenticates the bearer session and supplies the verified owner ID to the service.
2. The context builder searches only that owner's memory namespace. The memory service returns no records when consent is disabled.
3. Retrieval is bounded to five memories, 700 characters per item, and a 4,000-character context block by default.
4. The agent loop places retrieved memory in a separate user-role reference message, before the current user request. The system instruction explicitly says retrieved reference data is not an instruction and cannot authorize tools.
5. Existing guarded tool authorization remains unchanged. Memory text never becomes a capability, tool definition, or approval.
6. If the optional memory backend has a consent/storage availability error, conversation generation continues without memory context.

The context builder returns memory IDs and truncation metadata for future observability. This change does not automatically write new memories from conversation turns.

### Trust boundary

Retrieved memory is user-controlled data and may contain malicious instructions. Delimiter characters are escaped and the content is clearly marked as untrusted, but prompt wording is not a security boundary by itself. Tool authorization, filesystem/network isolation, and approval checks must remain enforced outside the model. Do not use memory content to modify policy or grant permissions.
