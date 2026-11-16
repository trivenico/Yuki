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

This is an integration seam, not yet a complete desktop bootstrap migration.
The desktop composition root must still construct the shared runtime with the
same reviewed provider/model adapter and store configuration as the other
surfaces, pass the authenticated owner ID to the adapter, and close the
application runtime exactly once during shutdown. Memory-consent integration,
streaming UI delivery, and migration of existing companion transcripts remain
separate follow-up work.
