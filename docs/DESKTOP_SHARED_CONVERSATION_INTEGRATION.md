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
The existing desktop launcher on the separate desktop-architecture branch still
creates a fresh `CompanionApplicationService` and in-memory `ConversationSession`
inside each `_open_session()` call. It does not construct
`ConversationApplicationRuntime`. Replacing only the controller call would
therefore leave two competing conversation engines and duplicate session state.

The migration must be performed at the composition root: establish the
relationship between the authenticated desktop `actor_id` and the runtime's
`owner_id`, construct the canonical runtime with its reviewed agent loop and
store, inject the adapter into the desktop controller, and make the lifecycle
close that runtime exactly once. The existing provider's `generate_chat()`
interface is not by itself proof that it satisfies the canonical agent-loop
model contract; the provider/model adapter must be explicitly verified before
composition. Keep desktop login/session revocation, memory-consent behavior, and
supervised task authorization intact during that migration.

Memory-consent integration and Qt-thread-safe streaming delivery remain
separate follow-up work. The legacy `ConversationSession` stores its transcript
only in process memory, so there is no durable legacy transcript database to
migrate; any still-running legacy session would need an explicit, bounded
handoff policy rather than an assumed on-disk migration.


## Desktop runtime composition helper

`ui.desktop.runtime.build_desktop_conversation_runtime()` now provides the
Qt-independent composition seam for the desktop surface. It accepts the
provider already created by the trusted desktop bootstrap and the authenticated
account ID returned by `DesktopSessionManager`. That ID is the stable
`AuthenticatedDesktopIdentity.actor_id` (which the session manager sources
from `AuthenticatedUser.user_id`), not the username or session token.

The helper builds the canonical `ProviderAgentAdapter`,
`AgentExecutionLoop`, deny-all `StructuredToolProtocol`, and
`ConversationApplicationRuntime`. It uses the configured conversation store,
sets the runtime owner scope, and registers the provider's optional `close()`
hook with runtime shutdown. Chat tools remain empty; this helper does not
connect the supervised specialist task runtime or grant desktop chat new
permissions. The desktop composition root still needs to call this helper,
construct `DesktopConversationAdapter` from the same runtime and owner ID,
and make the existing lifecycle close the resulting runtime exactly once.
