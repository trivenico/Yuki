# Conversation Generation Recovery Backlog Completeness Audit

## Summary

**Confirmed recovery-completeness defect:** `ConversationService._reply_unleased()` reads at most 100 running generation rows and, for each row, searches only the latest 200 conversation messages for its referenced user message. When that message is older than the 200-message window, the recovery loop silently skips the generation. The service does not verify that all running rows were reconciled before continuing with a new reply.

The store supports up to 5,000 messages per conversation, so the recovery lookup window is materially smaller than the supported history.

## Evidence

- `core/conversation_service.py`: recovery calls `list_interrupted_generations(..., limit=100)`; it then calls `list_messages(..., limit=200)` and searches the returned page for `user_message_id`. If no matching message is found, it skips recovery for that generation and proceeds.
- `core/conversation_store.py`: `list_interrupted_generations()` caps the limit at 100; `list_messages()` caps the limit at 200 and returns the latest messages when no cursor is supplied. Conversation message capacity is 5,000.

## Failure scenario

1. A durable generation row remains `running` while its user message is older than the latest 200 messages. This may arise in imported/repaired data or a partially reconciled legacy state; this audit does not claim it is a routine happy-path outcome.
2. A new reply starts recovery and retrieves the running row.
3. The service reads only the latest 200 messages, cannot find the referenced user message, and silently skips the generation.
4. It does not terminalize the row, emit a recovery-integrity error, or verify that the backlog is empty before proceeding with the new request.
5. The row can remain indefinitely marked `running`.

A backlog exceeding 100 rows is also only partially returned per pass; the current flow does not explicitly drain subsequent pages or signal incomplete recovery before proceeding.

## Impact

- Stale generation records can remain marked `running` indefinitely when their referenced message falls outside the bounded lookup.
- New replies may proceed despite unreconciled generation rows.
- Operational status and cleanup tooling may treat stale rows as active work.
- The production frequency of this condition is unknown.

This finding does not establish unauthorized execution, duplicate tool execution, or corruption of persisted messages.

## Recommended remediation

1. Replace the 200-message scan with an indexed point lookup by owner, conversation, and message ID, or a validated join from generation to message.
2. Page through running generations with a stable cursor until drained, or fail closed with an explicit recovery-incomplete response when the page limit is reached.
3. Treat a missing referenced user message as an integrity error; never silently skip it.
4. Make message persistence and generation terminalization atomic where practical, or ensure intermediate states are observable and idempotently recoverable.
5. Add metrics for running backlog size, oldest running age, recovery pages, missing references, and recovery failures.
6. Add tests for an old referenced message beyond 200 entries, more than 100 running generations, a genuinely missing message, and an injected mid-page failure followed by retry.

## Severity and confidence

- **Severity:** Moderate (P2), conditional data-lifecycle and operational correctness issue.
- **Confidence:** High for the bounded queries and silent-skip control flow; production occurrence is unverified.

## Review limitations

Source inspection at PR #1346 base head `c6d4bd5a17b8ffc2dead3a0161c3f0e7bad4f1ec`. No runtime code was changed, no tests were executed, and no production database or telemetry was inspected.
