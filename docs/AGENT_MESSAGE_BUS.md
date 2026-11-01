# Durable agent-to-agent messaging

`automation.agent_message_bus.AgentMessageBus` provides a small SQLite-backed
message transport for registered specialist agents. It is transport only:
message bodies are untrusted text, never executable instructions or tool calls.

## Example

```python
from automation.agent_message_bus import AgentMessageBus

bus = AgentMessageBus(
    "state/agent-messages.sqlite3",
    registered_agent_ids={"research", "engineering", "supervisor"},
    authorize=authorization_policy,
)

message = bus.send(
    actor_id="research",
    sender_id="research",
    recipient_id="engineering",
    thread_id="project-123",
    idempotency_key="research-to-engineering-001",
    message_type="finding",
    body="The API accepts an unbounded page size; please review.",
)

inbox = bus.inbox(actor_id="engineering", limit=50)
thread = bus.thread(actor_id="engineering", thread_id="project-123")
```

## Security and operational properties

- Sender identity must match the authenticated actor; spoofing is rejected.
- Both sender send permission and recipient receive permission are checked.
- Only explicitly registered agent IDs may participate.
- Inbox reads return only messages addressed to the requesting agent.
- Thread reads return only messages where the reader is a participant and still
  require the trusted authorization callback.
- SQLite transactions, a unique idempotency key, and payload digests prevent
  duplicate delivery and conflicting key reuse.
- Message body, identifiers, and page sizes are bounded.
- Authorization callback exceptions fail closed.
- Message content is untrusted data. The bus does not dispatch tools, run code,
  assign tasks, or grant permissions.

## Integration boundary

The application supplies the authenticated identity, registered-agent set,
and authorization policy. Supervisor routing, task assignment, delivery
notifications, retention/expiry, encryption-at-rest, and desktop/CLI
subscriptions remain separate application responsibilities. Deploy the
database on storage with appropriate OS permissions and backups.

## Tests

```bash
python -m unittest tests.test_agent_message_bus
```
