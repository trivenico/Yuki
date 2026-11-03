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


## Approved learning proposals

`core.learning_service.LearningService` adds a separate, per-authenticated-user proposal queue for lessons learned from completed tasks. It is a service layer, not an HTTP endpoint and is not automatically called by the agent loop.

1. A trusted caller submits a task reference and proposed lesson.
2. The service requires memory consent and stores a redacted, bounded pending proposal.
3. The owner can list pending proposals and explicitly approve or reject each one.
4. Approval writes the lesson to that owner's existing memory namespace using category `lesson`.
5. Rejection does not write personal memory. Approval is idempotent after the proposal has reached its approved state.

The service hashes the verified user ID for proposal ownership and never accepts a caller-selected owner ID. It redacts common API-key, bearer-token and password-like patterns, but redaction is best-effort and is not a substitute for avoiding secrets in task summaries.

**Important consistency boundary:** proposal metadata and personal memory currently live in separate storage systems (SQLite and the scoped JSON memory store). The approval operation serializes local calls and uses a SQLite write transaction, but cannot provide a true atomic transaction across both stores. A process or disk failure between the memory write and proposal commit can leave an orphaned lesson or require manual reconciliation. Do not claim distributed exactly-once semantics. A future transactional memory backend or durable outbox/idempotency key is required before multi-process production use.

The service does not train or fine-tune a model, change system prompts, alter identity/policies, grant tools, or autonomously modify Yuki. Approved lessons are retrievable personal reference data only.


## Episodic memory and reasoning context

`core.episodic_memory.EpisodicMemoryService` stores concise, explicitly supplied task summaries, outcomes and tags in a per-owner SQLite journal. It requires the existing memory-consent setting for recording and retrieval, supports owner-scoped search/list/delete, enforces a bounded retention count, and redacts common secret patterns. It does not automatically capture raw conversation history, model responses or tool payloads.

`MemoryContextBuilder` can optionally receive an episodic memory service. When configured, it retrieves relevant episodes alongside personal semantic/lesson memories and places both in the same bounded untrusted-reference block. Episodic consent failure is fail-closed for episodes but does not disable ordinary conversation or unrelated personal memory. Retrieved episode text is context only: it cannot alter system policy, authorize tools or grant permissions. The current search is bounded lexical overlap, not embeddings or temporal/causal reasoning.


## Stable Yuki identity profile

`companion.identity_core.IdentityCore` provides a local, versioned identity profile separate from model-provider configuration and user memories. It derives defaults from `yuki_identity.py`, preserves the canonical product name, validates a strict field allowlist, and supports explicit-approval updates with optimistic revision checks and reset. Writes use a same-directory temporary file, restrictive POSIX permissions, fsync and atomic replace.

The SHA-256 digest detects accidental corruption only; it is not a signature or protection against an attacker who can rewrite the profile and digest. Identity configuration does not grant tool access, alter security policy, or establish machine-verifiable consciousness. The current implementation is a local single-process file store; multi-process writers need an inter-process lock or transactional database.
