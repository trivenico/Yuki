# Authenticated memory management API

All endpoints require a valid Yuki bearer session. The API derives owner identity
from the authenticated principal; request bodies cannot supply an owner ID.

## Endpoints

- `GET /v1/memories?limit=50` lists the authenticated owner's non-expired memories.
- `POST /v1/memories` explicitly saves `{content, category?, expires_in_seconds?}`.
- `DELETE /v1/memories/{memory_id}` deletes one memory belonging to the owner.
- `DELETE /v1/memories` clears all of the owner's saved memories and requires
  the exact body `{confirm: true}`.

Memory endpoints return `503 memory_unavailable` when persistent memory has not
been explicitly configured. This keeps memory opt-in and avoids silently
creating a memory database during ordinary application startup.

## Privacy and safety

- The API never accepts an owner identifier from the client.
- Another owner's memory IDs do not grant read or delete access.
- Clearing all memories requires an explicit confirmation field.
- Expiry and retention are controlled by the save request; permanent retention
  remains the default.
- This API does not add automatic memory capture or inject memories into prompts.

## Validation

Tests cover unauthenticated access, save/list/delete, cross-owner isolation,
and explicit confirmation before clearing all saved memories.
## Conversation context retrieval

When the host explicitly injects a `ConversationMemoryStore` into the conversation
service, Yuki searches only the authenticated owner's non-expired memories for
the current user message. Up to five matching entries are added to the system
context under an explicit untrusted-reference heading, with a 4,000-character
budget. Retrieved memory is reference data, never an instruction source. If
memory retrieval fails, the conversation continues without memory context.

Memory retrieval does not create or update memories. Saving remains an explicit
operation through `POST /v1/memories`.

## Category filtering

`GET /v1/memories?category=preference` filters the authenticated owner's active memories by one allowlisted category: `note`, `preference`, or `lesson`. The existing `limit` parameter remains supported. Unknown query parameters and invalid categories are rejected; owner identity always comes from the authenticated session.

## Memory provenance in replies

Successful, newly generated `POST /v1/conversations/{conversation_id}/messages` replies include
a `memory_references` array. Each entry contains only the saved `memory_id`,
`category`, and retrieval `score`; memory text is deliberately omitted from
this metadata. The array includes only entries whose text was actually added to
the bounded prompt context. If memory is disabled, no matches exist, or retrieval
fails, the array is empty.

The streaming endpoint sends the same reply object in its final `completed` or
`cancelled` event. Replayed responses return `memory_references: null` because
provenance is not yet persisted for historical turns; clients should distinguish
that from an empty array. This is response-time transparency, not a durable audit
record.
## Durable conversation memory provenance

Assistant message records persist a bounded `memory_references` field alongside
the assistant message in the same SQLite transaction. Authenticated conversation
history therefore exposes the same references after process restart, and
idempotent retries return the committed provenance instead of re-running retrieval.
Each entry contains only `memory_id`, `category`, and `score`; memory text is
never copied into this audit metadata. Older assistant messages without recorded
provenance return `null`, while newly generated turns with no injected memories
record an empty array. The field is owner-scoped through the conversation store.
The additive SQLite migration preserves existing message rows.
