# Phase 160 — Owner-scoped memory in the authenticated web runtime

## Runtime composition

The local web launcher now composes the following components:

1. Authenticated conversation request resolves the owner from the server-side bearer session.
2. `ConversationService` invokes `agent_loop_factory(owner_id)` only after ownership, idempotency, and version checks.
3. `providers.reasoning_runtime` hashes the authenticated owner ID into a stable opaque `owner-<sha256>` memory key. Client-provided actor IDs are never accepted.
4. `AgentMemoryStore` enforces private owner scope and the web authorization callback permits only `agent_memory:read` and `agent_memory:write` for the opaque owner key and the `assistant` agent.
5. Shared memory remains denied; the callback does not grant `agent_memory:share`.
6. `AgentMemoryRetriever` asks the authorized store for candidate records before consulting the embedding cache. Cached vectors therefore cannot widen visibility.
7. `ReasoningAgentAdapter` places only server policy in the system message. Preferences and memory are serialized as untrusted reference data.
8. The web profile exposes no tools. The `GuardedToolRuntime` policy denies all tool execution.

## Local embeddings

No embedding model is downloaded automatically.

- Default: `LocalHashingEmbedder`, a deterministic, offline lexical hashing baseline.
- Optional: set `YUKI_EMBEDDING_MODEL_PATH` to an already-present local Sentence Transformers model directory.
- Optional: set `YUKI_EMBEDDING_DEVICE` to `cpu`, `cuda`, or `mps`.
- Optional: set `YUKI_EMBEDDING_MODEL_ID` when intentionally versioning a local model cache identity.

The persistent SQLite embedding index shares the memory database file and keys records by actor, memory ID, model identity, and content SHA-256. Changing content or model identity invalidates the cached vector.

The hashing fallback is not a trained semantic model and should not be described as equivalent to a sentence-transformer. Install and configure a suitable local model for stronger semantic recall.

## Validation

Regression tests cover deterministic fallback embeddings, local-only Hugging Face loading, owner-specific memory keys, private-memory non-disclosure across owners, shared-memory denial, and web-launcher composition.

The authenticated API now exposes `GET /v1/memory` and `POST /v1/memory`. POST accepts exactly `{\"content\": string}` and always creates a private, unverified owner note with a server-assigned source; clients cannot set actor IDs, scope, confidence, or verification state. GET returns only the authenticated owner's private records. The browser currently has no memory-management UI controls. Correction, lifecycle review, and deletion endpoints remain intentionally unexposed until their approval and audit flows are integrated.