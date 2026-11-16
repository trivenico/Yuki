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
`cancelled` event. Replayed responses return the persisted references for turns
generated after this migration; older assistant messages return
`memory_references: null`. Clients should distinguish that from an empty array.
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


## Encrypted owner-scoped export

`POST /v1/memories/backup` accepts exactly `{password}` and returns a versioned
`yuki-memory-backup` envelope as base64. The authenticated session determines the
owner; `owner_id` is never accepted from the request. The encrypted snapshot
contains the owner binding, export timestamp, schema version, count, and a
stable ordering of that owner's active memories. Expired memories and other
owners' records are excluded. The snapshot is encrypted before it is returned;
the API response is marked `Cache-Control: no-store`.

Export snapshots are bounded before serialization: at most 2,000 active memories per owner and at most 8 MiB of combined UTF-8 memory content. The encrypted-envelope layer independently enforces its serialized payload and envelope size limits.

The passphrase must be at least 12 UTF-8 bytes and is not recoverable by Yuki.
Keep it separate from the backup file. The endpoint requires the optional
`production-storage` dependency extra for AES-GCM support and returns
`503 memory_backup_crypto_unavailable` when that dependency is absent.
There is not yet a restore endpoint; importing backups remains intentionally
unsupported until preview, validation, owner binding, and atomic merge semantics
are implemented.

## Read-only encrypted backup preview

`POST /v1/memories/backup/preview` accepts exactly
`{password, envelope_base64}`. It authenticates and decrypts the envelope,
strictly validates the versioned snapshot (including duplicate JSON keys,
unknown fields, record bounds, categories, timestamps, duplicate IDs, and
owner binding), and compares incoming memory IDs with the authenticated
owner's existing records. The response contains only counts and category
totals; it never returns memory content.

The preview performs no database writes. It does not import, overwrite, merge,
or delete memories. The endpoint is bounded to a 17 MiB JSON body to carry the
maximum supported base64 envelope and returns `Cache-Control: no-store`.
A separate, explicitly confirmed restore workflow is still required before
any import can occur.

Conflict counts currently identify exact memory-ID collisions only; preview does
not infer semantic or near-duplicate content. Such similarity checks must remain
advisory and must not silently suppress or overwrite memories during a future restore.
