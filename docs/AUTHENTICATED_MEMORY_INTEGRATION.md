# Authenticated memory and cognitive context

This integration connects the owner-scoped memory service to the authenticated conversation API. It is opt-in and does not automatically save model output.

## Consent lifecycle

Memory consent is disabled for a user until explicitly enabled.

- `GET /v1/memory/consent` returns `{"enabled": false}` or `{"enabled": true}`.
- `PUT /v1/memory/consent` accepts exactly `{"enabled": true}` or `{"enabled": false}`.
- Disabling consent stops future retrieval and writes. It does not silently delete existing records.
- `DELETE /v1/memory/{memory_id}` explicitly deletes one record. Deletion remains available after consent is disabled.

All routes require the normal authenticated bearer session. The API derives the memory owner from that session; request bodies cannot select or override an owner.

## Memory records

- `GET /v1/memory` returns the authenticated user's records (up to 50); when consent is disabled, the service returns an empty list.
- `POST /v1/memory` accepts `{"text": "...", "category": "note"}`. The category is optional and defaults to `note`.
- A write without consent returns HTTP 403 with `memory_consent_required`.
- `DELETE /v1/memory/{memory_id}` returns `{"deleted": true}` only when a record owned by the authenticated user was deleted.

Memory text and categories are bounded and validated by the underlying scoped-memory service. A caller cannot read or delete another user's records by supplying an owner identifier.

## Conversation context

The shared conversation runtime composes a deterministic `CognitiveContextEngine` with a bounded `MemoryContextBuilder` when a memory service is configured. For each new turn:

1. The conversation service supplies the authenticated owner ID and current user text.
2. The cognitive engine prepares a bounded, inspectable brief using the deterministic planner and consent-aware memory retrieval.
3. The brief is serialized as untrusted reference context and appended to the system context only if it fits the configured limits.
4. The existing guarded agent execution loop remains responsible for model calls and all tool authorization/execution.

The cognitive brief proposes a task decomposition only. It does not dispatch specialist agents, execute tools, grant permissions, or change security policy. Retrieved memory and plan descriptions are untrusted data, not instructions.

If cognitive preparation or memory retrieval fails, the conversation proceeds without the additional context. No partial memory context is injected.

## Current boundaries

- This is a local/service integration foundation, not a complete memory management UI.
- Memory retrieval is bounded and deterministic; it is not a guarantee of semantic relevance.
- Memory writes are explicit API calls. Model-generated statements are never automatically persisted.
- Consent revocation blocks future use but does not erase stored records; use the explicit delete endpoint for deletion.
- API deployment still requires TLS, secure bearer-token handling, private database storage, backups, and normal operational monitoring.
