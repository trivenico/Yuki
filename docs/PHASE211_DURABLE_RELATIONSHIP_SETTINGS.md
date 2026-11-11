# Phase 211 — durable relationship settings

**Status:** Persistence foundation implemented on a branch stacked on the transactional companion-state work. It is not yet integrated into the running companion API or desktop app.

## Implemented

- Persist validated relationship preferences in SQLite, scoped by the local owner ID.
- Safe defaults when no row exists; explicit reset deletes saved preferences.
- Atomic upsert under an immediate transaction.
- Strict payload shape and enum validation; corrupted or unknown payloads fail closed.
- Database symlink checks and POSIX mode 0600 defense-in-depth.
- Tests for owner isolation, round-trip persistence, reset, invalid settings, corrupt payloads and filesystem protections.

## Security and limitations

- This database is **not encrypted at rest**. File permissions are not encryption.
- The owner ID is a local storage scope, not authentication.
- Adult confirmation remains a boolean attestation, not age verification.
- Persistence does not enable relationship memory, schedule proactive messages, send notifications, inject persona guidance, or grant tools/device access.
- No calling features are present.
- Integration into the shared companion runtime, authenticated settings API, explicit audit/deletion UX, encrypted backup/restore, age assurance and conversation evaluations remain required.

## Acceptance gates

1. Integrate the store into the shared companion runtime with safe lifecycle/close semantics.
2. Expose authenticated owner-only read/update/reset operations; never accept an owner ID from request bodies.
3. Add concurrency and malformed-request tests plus audit/deletion semantics.
4. Add relationship-boundary evaluations before enabling the mode in a product UI.
