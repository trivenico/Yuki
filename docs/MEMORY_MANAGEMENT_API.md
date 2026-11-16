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
