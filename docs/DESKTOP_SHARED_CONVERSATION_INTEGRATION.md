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

This adapter is now wired into the desktop launcher's authenticated-session
composition on the integration branch. The launcher verifies the same signed-in
identity, builds the legacy specialist controller, and replaces only the chat
surface with the shared durable runtime. The legacy controller continues to
own specialist plans, approvals, task operations, encrypted backup/restore,
and migration. The launcher loads the same application configuration used by
the provider bootstrap and passes the encrypted desktop memory facade to the
consent-aware chat adapter.

The provider remains owned by the existing process lifecycle; the conversation
session bundle owns its store and is closed through the controller bridge on
logout, expiry, and application shutdown. Desktop chat receives an empty tool
list and does not inherit specialist execution permissions. This is an
integration cutover under CI review, not a claim that the full desktop
application has passed end-to-end platform validation.

Qt-thread-safe streaming delivery remains follow-up work. The legacy
`ConversationSession` stores its transcript only in process memory, so there is
no durable legacy transcript database to migrate; any still-running legacy
session would need an explicit, bounded handoff policy rather than an assumed
on-disk migration.


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
hook with runtime shutdown by default. When an outer desktop lifecycle already owns
provider shutdown, the caller must pass `close_provider=False` so the provider is
closed exactly once by that lifecycle. Chat tools remain empty; this helper does not
connect the supervised specialist task runtime or grant desktop chat new
permissions. The desktop composition root calls this helper through
`build_shared_desktop_controller()`; the authenticated adapter derives its
owner scope from the same verified identity.


## Authenticated desktop session facade

`ui.desktop.authenticated_conversation.AuthenticatedDesktopConversation`
binds the shared adapter to the identity returned by the desktop session
manager. It derives the owner scope from the verified identity's `actor_id`,
rechecks the live session before every operation, guards streaming callbacks,
and refuses to return a response if the session expires during inference.
The facade does not own or close the shared runtime; the desktop lifecycle
remains its single shutdown owner. The existing desktop memory-consent and
specialist-task approval flows must remain separate and unchanged during the
eventual bootstrap migration.


## Consent-gated memory context

`core.memory_context.build_memory_context_system_text()` provides a bounded,
backend-agnostic adapter for memory facades. It reads no entries unless the
facade reports `context_enabled is True`; it requests at most ten ranked
entries, accepts only the supported note/preference/lesson categories, bounds
the serialized context, and marks every saved entry as untrusted reference
data rather than instructions. If consent is enabled but retrieval or
validation fails, the turn is stopped before the model is called.

`DesktopConversationAdapter` accepts an optional memory facade and injects
only that consented context into the current model turn. It does not write,
migrate, or delete memories. `AuthenticatedDesktopConversation` exposes
session-checked `set_memory_context_enabled()` and
`memory_context_enabled()` controls. The local consent can still be revoked
during logout after session revocation through `disable_memory_context()`.

The desktop composition root must pass the existing encrypted
`create_desktop_memory()` instance into this facade and wire the memory toggle
to these methods. It must not construct a plaintext memory store or treat the
toggle's UI state as proof of consent. Memory writes remain a separate explicit
user action; this integration only controls whether saved entries may be
retrieved for model context. The core adapter is deliberately storage-agnostic
so other surfaces can reuse it with their own authenticated and consent-aware
memory facade.


## Authenticated session bundle and lifecycle ownership

The composition root can use
`ui.desktop.shared_conversation_session.build_authenticated_desktop_conversation()`
to construct the complete authenticated durable-chat unit. It verifies that
`sessions.current_identity()` matches the supplied identity before opening the
conversation store, derives the owner only from `identity.actor_id`, passes
through the encrypted memory facade, and returns a `DesktopConversationSession`.

Register that returned session as one resource in the desktop lifecycle. Its
`close()` invalidates the authenticated facade (revoking memory context and
clearing the selected conversation) before closing the conversation runtime.
The helper deliberately sets `close_provider=False`: the outer desktop
lifecycle continues to own the already-created provider and closes it exactly
once. Do not also register the inner runtime separately, or call both
`DesktopConversationSession.close()` and `runtime.close()` from independent
shutdown paths.

Example composition at the authenticated desktop boundary:

```python
memory = create_desktop_memory(sessions.keyring)
conversation_session = build_authenticated_desktop_conversation(
    load_config(),
    provider=provider,
    identity=identity,
    sessions=sessions,
    memory=memory,
)
conversation = conversation_session.conversation
# Register conversation_session (not conversation_session.runtime) with the
# lifecycle; register provider once in the outer lifecycle as before.
```

The bundle is now covered by composition tests for verified identity,
owner-scoped durable history, deny-all chat tools, external provider ownership,
memory context, and idempotent shutdown. The launcher/controller migration is now present on the controlled integration
branch. CI and cross-platform desktop validation remain outstanding.


## Qt-facing chat controller seam

`ui.desktop.shared_chat_controller.SharedDesktopChatController` is the
Qt-independent controller intended to replace the launcher's current
`controller.respond(message)` chat call. It accepts the
`DesktopConversationSession` bundle, delegates turns to its authenticated
conversation facade, exposes the existing memory-context toggle through the
facade's consent checks, and closes the bundle on logout. It never receives a
provider, owner ID, raw session token, or tool registry.

The launcher constructs this controller only after
`DesktopSessionManager.current_identity()` has returned a live identity, passes
the existing encrypted `CompanionMemory` instance to the composition helper,
and routes bundle shutdown through the controller while the outer lifecycle
continues to own the provider.
Existing specialist-plan and task APIs remain on the current authenticated
specialist controller/service until a separate adapter migration is reviewed;
the chat controller does not gain authority to approve or execute tasks.

The Qt `ChatWorker` can continue to call `respond(message)` from its
background thread. If streaming is enabled, its delta callback must emit a Qt
signal; never update widgets directly from the provider worker. Logout must
invalidate the conversation facade before closing the store, and the outer
lifecycle must continue to close the provider exactly once.


## Preserve the existing specialist UI during the chat cutover

`ui.desktop.shared_desktop_controller.SharedDesktopControllerBridge` provides
the old desktop controller surface while moving only chat and memory-context
operations to the shared durable runtime. Chat requests and memory consent go
to `SharedDesktopChatController`; specialist plans, dispatch, approvals,
task progress/cancellation, migration and encrypted backup operations remain
on the existing authenticated `DesktopCompanionController`. The bridge uses
an explicit method allowlist rather than exposing the legacy controller through
unrestricted attribute forwarding.

In the desktop launcher's authenticated-session composition, the intended
wiring is:

```python
context = build_desktop_context(
    authenticate=sessions.current_identity,
    provider=provider,
    memory=create_desktop_memory(sessions.keyring),
    session=ConversationSession(provider),
    agent_coordinator=coordinator,
    task_runtime=task_runtime,
)
legacy_controller = DesktopCompanionController(
    context.application, context.identity, sessions
)
conversation_session = build_authenticated_desktop_conversation(
    load_config(),
    provider=provider,
    identity=context.identity,
    sessions=sessions,
    memory=context.application.memory,
)
shared_chat = SharedDesktopChatController(conversation_session)
self.controller = SharedDesktopControllerBridge(shared_chat, legacy_controller)
```

This keeps the current specialist/task authorization implementation intact
while replacing the chat engine. The desktop worker's existing
`controller.respond(message)` call remains compatible. Logout revokes the
legacy authenticated session first and then always closes the shared chat
bundle, even if revocation reports an error. This wiring is implemented in `ui/desktop/launch.py` on the integration
branch. The two source histories were reconciled by selective file integration,
not by a blind merge.

## Shared desktop composition factory

The `ui.desktop.shared_desktop_composition.build_shared_desktop_controller()` factory now centralizes the cutover wiring. It creates the authenticated durable conversation bundle, wraps it in `SharedDesktopChatController`, and combines that with the existing authenticated specialist controller through the allowlisted `SharedDesktopControllerBridge`.

The desktop composition root should call this factory after local login and pass the same verified identity, session manager, provider, and encrypted memory facade used by the existing desktop services. The returned bridge owns the conversation bundle and delegates logout to the specialist controller; the outer process lifecycle continues to own the provider and must not also register the inner conversation runtime. The factory rejects an incomplete specialist contract and closes the newly-created conversation bundle if bridge construction fails.

This isolates the remaining launcher port to replacing `_open_session()`'s controller construction and registering the returned bridge as the authenticated session resource. The desktop launcher itself is still on the separate, diverged desktop-foundation branch; no claim is made that its Qt entrypoint has already been migrated.

## Session-expiry cleanup behavior

The shared desktop controller bridge now treats an authorization failure from either the durable-chat facade or a delegated specialist/task operation as a terminal desktop-session event. It closes the bridge authorization gate, invokes the legacy controller's logout to revoke the local session, and closes the shared conversation bundle even if revocation fails. This prevents an expired session from leaving the durable store, memory-consent facade, or active chat workers alive while the UI waits for the next login transition. The original `PermissionError` is preserved when cleanup succeeds; cleanup failures are surfaced as a separate runtime error. This is a fail-closed boundary: callers should return the UI to login after such an authorization error and must not silently retry under the expired identity.


## Launcher cutover on the integration branch

The authenticated `_open_session()` path now builds the shared durable chat controller with the verified local identity and the existing encrypted memory instance. The previous `CompanionApplicationService` remains instantiated as the specialist/task/backup/migration authority, but its in-memory chat response path is no longer the launcher's chat authority. The Qt worker still invokes the compatible `respond(message)` method. The conversation session is closed by bridge logout; the process lifecycle closes the provider once. The launcher imports its configuration and encrypted-memory factory in the Qt run scope so the session composition does not depend on names local to the separate service factory.
