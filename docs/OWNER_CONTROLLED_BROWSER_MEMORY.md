# Owner-controlled browser memory

Browser memory management is available only when the local memory feature is explicitly enabled.

## Enablement

Set `YUKI_MEMORY_ENABLED=true`, configure `YUKI_EMBEDDING_MODEL_PATH` and `YUKI_EMBEDDING_MODEL_ID`, and keep the embedding model on disk. The web runtime fails closed if the opt-in memory configuration is invalid. Memory remains disabled by default.

## Authenticated API

All endpoints require the normal Yuki bearer session. The owner is always derived from that session; clients cannot submit an actor or owner ID. The browser API manages private memories for the `yuki` agent only.

- `GET /v1/memory` — list the authenticated owner's unexpired private memories.
- `GET /v1/memory?q=...` — search the owner's private memories.
- `POST /v1/memory` — explicitly save a memory. Body accepts `content`, optional `category`, and optional `retention_days`. The server fixes source to `owner_explicit_save`, sets private scope, and leaves the record unverified pending owner review.
- `POST /v1/memory/{memory_id}/review` — approve or reject with `status` and a required `reason`.
- `POST /v1/memory/{memory_id}/retire` — retire with a required `reason`.
- `POST /v1/memory/{memory_id}/renew` — extend retention with `retention_days` and a required `reason`.
- `POST /v1/memory/{memory_id}/correct` — replace a memory with corrected `content`, `source`, and a required `reason`.
- `DELETE /v1/memory/{memory_id}` — delete the owner's memory.

Memory capture is never automatic. A save is an explicit user action and remains unverified until the owner approves it. Shared-memory management is intentionally not exposed through this browser API. Store-level authorization is checked on every operation, so disabling an account or revoking its capability takes effect without relying on cached role claims.

Retrieved memory remains untrusted reference data. It cannot change system policy or grant tool permissions. Browser tools remain disabled in the initial web profile.
