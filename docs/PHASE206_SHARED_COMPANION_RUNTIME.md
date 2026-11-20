# Phase 206 — shared local companion application runtime

**Status:** Experimental integration foundation; not a production-ready companion release.

## Implemented

- Composes the shared conversation runtime with Yuki persona, tone state, consent-gated local identity labels, reminder queue, and optional local memory.
- Uses the stable local owner identity for the conversation store. This is a local data-scope identifier, not a network authentication credential.
- Places companion state beneath the configured workspace companion directory.
- Memory is disabled by default and only constructed when companion_memory_enabled is explicitly set to boolean true.
- Identity labels require explicit consent; no biometric templates are stored.
- Reminder queue emits due reminders once and never executes arbitrary actions.
- Shutdown delegates to the shared application runtime and is idempotent.

## Deliberate limitations

- This is a composition boundary, not a continuously running companion daemon.
- No OS notification delivery, background scheduler, wake-word listener, microphone activation, device control, or calling functionality is started.
- Persona/tone guidance is exposed through companion_guidance() but is not silently injected into the agent loop. A model adapter must deliberately include it.
- Existing JSON state stores are local workspace files. They are not encrypted at rest, do not provide cross-process locking, and need a separately reviewed migration to a transactional owner-scoped store before multi-process or shared-device use.
- Memory is opt-in, but the current file store does not provide encryption, retention scheduling, or semantic retrieval.
- No native desktop lifecycle, OS credential store, signed updater, crash recovery, or notification permission flow is included.

## Validation

Focused tests cover shared runtime composition, default-off memory, explicit memory opt-in, consent-gated identities, one-time due reminder delivery, workspace requirements, and idempotent shutdown. Exact-head CI is required. No local test execution, real-device validation, or production deployment is claimed.
