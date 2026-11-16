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