# Phase 211 — durable relationship settings

**Status:** Persistence and local companion-runtime composition implemented on a draft branch stacked on the transactional companion-state work. Not yet exposed through the authenticated network API or desktop app.

## Implemented

- Persist validated relationship preferences in a dedicated SQLite database, scoped by the stable local owner ID.
- Compose the store into `CompanionApplicationRuntime` and expose it as `runtime.relationship_settings`.
- Safe defaults when no row exists; explicit reset deletes saved preferences.
- Atomic upsert under an immediate transaction.
- Strict payload shape and enum validation; corrupted or unknown payloads fail closed.
- Database symlink checks and POSIX mode 0600 defense-in-depth.
- Runtime restart test confirms settings survive reconstruction.
- Runtime does not silently inject relationship persona guidance into the model loop.

## Security and limitations

- This database is **not encrypted at rest**. File permissions are not encryption.
- The owner ID is a local storage scope, not authentication.
- Adult confirmation remains a boolean attestation, not age verification.
- Memory and proactive check-in flags are preferences only; no relationship-memory service, scheduler, notification delivery or prompt injection is implemented.
- No tools, device authority, approval bypass or calling features are added.
- No authenticated settings API, desktop UI, encrypted backup/restore, age assurance or conversation evaluations are included.

## Acceptance gates

1. Expose authenticated owner-only read/update/reset operations; never accept an owner ID from request bodies.
2. Add concurrent-update and malformed-request tests plus explicit audit/deletion semantics.
3. Add relationship-boundary evaluations before enabling the mode in a product UI.
4. Complete age-assurance, privacy and safety review before romantic-mode activation.
