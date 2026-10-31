# Phase 105 — shared conversation runtime

## Delivered

- Added `core/conversation_runtime.py` as the shared provider-backed conversation service.
- Runtime owns workspace-scoped session creation/loading, bounded recent-history assembly, atomic durable turn acquisition, provider invocation, and persisted completion/failure.
- Persistent CLI text and NDJSON modes now call the same runtime instead of maintaining a separate persistence implementation.
- Replaying a completed request ID returns the stored assistant answer without another model call.
- Replaying a running request ID fails closed and does not issue a duplicate provider call.
- Failed requests remain recorded; retries use a new request ID.
- System instructions are not persisted as conversation messages. Conversation history is explicitly untrusted context.
- Tool execution remains disabled in this runtime. Model output cannot grant permissions or invoke skills/tools.

## Runtime boundary

`ConversationRuntime` is an interface-independent service. Desktop and hosted API adapters can call it without duplicating session logic. They must supply a trusted workspace identity derived from their authenticated principal; a client-provided workspace string is not authentication.

## Current limits

- Provider interface is synchronous `is_available()` + `generate()`; actual token streaming is not implemented.
- Context budgeting is character-bounded, not tokenizer-aware.
- No tool-call loop, specialist-agent delegation, approval UI, or task-runtime dispatch is connected.
- No stale conversation-turn lease/heartbeat recovery worker. Existing running turns fail closed on replay.
- No hosted authentication, encryption-at-rest, retention/deletion policy or multi-device sync.

## Validation

```bash
python -m unittest tests.test_conversation_store -v
python -m unittest tests.test_conversation_runtime -v
python -m unittest tests.test_yuki_cli -v
python -m unittest discover -s tests -v
```

Require exact-head CI on Linux and Windows before marking ready.
