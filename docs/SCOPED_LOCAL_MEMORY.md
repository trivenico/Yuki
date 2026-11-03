# Per-Actor Local Memory

## Scope

`core.scoped_memory.ScopedMemory` provides a local filesystem namespace for one actor. It builds on the existing `MemoryStore` and `VectorMemory`; it does not create a new database or duplicate the retrieval algorithm.

## Identity boundary

The `actor_id` constructor argument MUST originate from a verified server-side identity, such as the authenticated identity resolver. It MUST NOT come from a request body, query parameter, cookie value that has not been validated, or a client-supplied header.

This class is a storage isolation primitive, not an authentication or authorization system. The current mainline web conversation service is not automatically wired to this class. Do not add such wiring until the authenticated identity work is integrated and reviewed.

## Namespace and filesystem behavior

- Actor identifiers are converted to SHA-256 directory names; raw identifiers are not written into path names.
- Each actor receives a separate `memories.json` file under the configured root.
- The namespace directory is created with restrictive POSIX mode where supported.
- Resolved namespace paths are checked to remain directly under the configured root.
- Retrieval and mutation share one `MemoryStore` instance and lock within the facade.
- This remains a single-process JSON store. The in-process lock does not provide cross-process transaction safety or distributed locking.

## Privacy and lifecycle

- Writes remain explicit calls to `remember`; ordinary chat messages are not captured automatically.
- `list`, `search`, and `forget` operate only within the selected actor namespace.
- The caller remains responsible for authorization, consent, export workflows, retention policy, and backup encryption.
- Hashing actor IDs hides the literal identifier in filenames but is not encryption and does not protect memory contents at rest.
- Multi-tenant deployments should use a transactional database with tenant-scoped keys, OS/service-account isolation, encrypted backups, and explicit retention controls before production rollout.

## Before conversation integration

1. Integrate the verified identity resolver from the Phase 2 work; never trust client actor IDs.
2. Construct a new `ScopedMemory` for each authenticated request or use a rigorously tenant-scoped service factory.
3. Add consent and no-memory controls to the authenticated account settings.
4. Ensure retrieved memory is untrusted context and never changes tool permissions.
5. Add authenticated inspect, export, edit, delete, and retention APIs.
6. Test concurrent requests, cross-process behavior, tenant isolation, backup/restore, and deletion guarantees.

## Retrieval semantics

The current retrieval engine is deterministic BM25-style lexical ranking over Unicode tokens. It is not embedding-based semantic search. A future local embedding index can supplement, but must not replace, the offline lexical fallback.
