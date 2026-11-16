# Desktop shared conversation integration

The native desktop surface must use the canonical
`ConversationApplicationRuntime` rather than instantiate a second
`ConversationSession` or provider-owned chat history.

`ui.desktop.conversation_adapter.DesktopConversationAdapter` is the narrow
surface adapter for that integration:

- The desktop authentication boundary supplies the verified local actor ID as
  `owner_id`; the adapter never accepts an owner ID per message.
- Conversation creation, ownership checks, history, optimistic versions,
  idempotent retries, generation leases, and interrupted-turn recovery remain
  in `ConversationService` and its configured store.
- Chat sends an empty tool list. Desktop chat therefore does not gain new
  execution permissions; supervised agent plans remain a separate, explicitly
  reviewed workflow.
- `clear_selection()` drops only the active UI conversation reference. It
  does not delete stored messages or backups.
- The adapter is Qt-independent so it can be tested and reused by desktop UI
  controllers without importing presentation dependencies into core services.
- Sends are serialized per adapter to preserve the canonical conversation
  version sequence. The adapter snapshots the selected conversation under its
  short-lived state lock, then releases that lock before model inference; UI
  selection changes therefore remain responsive while a response is running.
- The UI must invoke `send()` from a background worker. It may provide a
  thread-safe cancellation predicate and a text-delta callback; callbacks must
  marshal UI updates onto the Qt main thread.

This is an integration seam, not yet a complete desktop bootstrap migration.
The desktop composition root must still construct the shared runtime with the
same reviewed provider/model adapter and store configuration as the other
surfaces, pass the authenticated owner ID to the adapter, and close the
application runtime exactly once during shutdown. Memory-consent integration and Qt-thread-safe streaming delivery remain
separate follow-up work. The legacy `ConversationSession` stores its transcript
only in process memory, so there is no durable legacy transcript database to
migrate; any still-running legacy session would need an explicit, bounded
handoff policy rather than an assumed on-disk migration.
