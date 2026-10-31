# Phase 104 — durable conversation turn lifecycle

## Goal

Make persistent CLI conversation turns atomic and safe under process restarts and overlapping requests. This phase strengthens the persistence boundary; it does not yet turn the CLI single-turn provider call into the full guarded tool-using agent runtime.

## Implemented

- Add a durable `conversation_turns` table with request ID, workspace, session, user-message reference, optional assistant-message reference, lifecycle status, timestamps and bounded failure code.
- `begin_turn` atomically stores the user message and acquires the session's single active-turn guard.
- Concurrent requests cannot start another turn in the same conversation while a prior turn is running.
- Request IDs are idempotent and bound to the exact workspace, session and original user content.
- `complete_turn` atomically persists the assistant response and closes the turn.
- `fail_turn` records a bounded provider failure while preserving the original user request for inspection/retry.
- CLI persistent runs use the atomic lifecycle for both text and NDJSON output.
- `list_turns` exposes bounded lifecycle history for future desktop/task views.

## Operational behavior

If a process exits after `begin_turn` but before completion, the turn remains `running`. This intentionally fails closed: the next request receives an active-turn conflict rather than silently running a duplicate model call. A later recovery worker should reconcile stale turns using an explicit lease/heartbeat policy and operator-visible retry action. Do not automatically mark a turn failed solely based on age without a recovery policy.

## Security boundaries

- Workspace IDs remain caller-supplied local partition keys, not authenticated identity.
- Conversation content is untrusted context and never grants tool permissions or approvals.
- This CLI path still performs one provider generation call with tool execution disabled.
- A turn request ID is an idempotency/correlation key, not a credential.
- No encryption-at-rest, hosted identity middleware, desktop sync, retention/deletion policy, or stale-turn recovery worker is delivered here.

## Validation

```bash
python -m unittest tests.test_conversation_store -v
python -m unittest tests.test_yuki_cli -v
python -m unittest tests.test_capability_manifest -v
python -m unittest discover -s tests -v
```

All exact-head Linux/Windows checks are required before this phase is considered ready.
