# Conversation crash recovery operations

Yuki recovers interrupted conversation generations while holding the conversation's
durable generation lease. Recovery never automatically replays a model/tool sequence
whose external side effects may already have occurred.

## Durable recovery audit

Each successfully finalized recovery writes one append-only
`conversation_recovery_events` row in the same SQLite transaction that changes
the generation from `running` to its terminal state. The record contains only:

- authenticated owner and conversation scope;
- request ID;
- recovery outcome (`completed`, `interrupted`, or `orphaned`);
- count of tool executions changed from `started` to `uncertain`;
- recovery timestamp.

It does not contain tool arguments, model output, provider references, or raw tool
results. A unique generation constraint prevents duplicate audit events.

## Owner API

`GET /v1/conversations/{conversation_id}/recovery-events?limit=50`

The authenticated session determines the owner. The optional limit must be an
integer from 1 to 100. The endpoint returns sanitized recovery metadata only;
it cannot inspect another owner's conversation.

## Bounded recovery

A pass processes at most 100 interrupted generations. A full page is treated
conservatively as a potentially saturated backlog: the current request does not
start fresh model work. A subsequent request obtains the lease and checks the
remaining backlog. This avoids unbounded recovery work inside a foreground
conversation request.

Recovery writes are fenced by the current, unexpired generation lease. Final
generation status and its audit event are committed atomically. If the audit
insert fails, the terminal status update rolls back as well, leaving the
generation eligible for a later recovery attempt.

## Operational interpretation

- `completed`: the generation's own assistant message had already been durably
  persisted before the crash.
- `interrupted`: no matching assistant response was persisted; Yuki added an
  interruption notice and did not replay potentially side-effecting work.
- `orphaned`: the generation references a missing user message; Yuki finalized
  it without fabricating a conversation turn.
- `uncertain_tool_calls`: number of started tool journal entries changed to
  uncertain during this recovery. It is not proof of provider completion or
  non-completion.
