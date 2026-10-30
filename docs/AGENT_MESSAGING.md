# Agent-to-agent messaging

Yuki's specialist workforce can exchange bounded, durable text messages through `automation.agent_messaging.AgentMailbox`.

- SQLite persistence and an inbox index support restart-safe delivery.
- Send operations are idempotent; reusing a key with a different payload is rejected.
- Send, read, and acknowledge are separately authorized with `agent:message:send`, `agent:message:read`, and `agent:message:ack`.
- Inbox reads are recipient-scoped and limited to 1-100 messages.
- Message bodies are limited to 8,000 characters.
- Message content is untrusted data. Delivery does not execute content, grant tools, or change agent permissions.
- Use the same durable SQLite database path as the task runtime and agent coordinator when integrating the mailbox.

This is the persistence/API foundation; automatic coordinator delivery, event streaming, and UI integration remain follow-up work.
