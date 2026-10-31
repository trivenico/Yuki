# Yuki shared application runtime

## Purpose

Provide one application lifecycle and chat service boundary for the Windows CLI, future Windows desktop, and Oracle Linux deployment. User interfaces must not duplicate provider construction, agent calls, conversation persistence, or conversation selection.

## Current implementation

- `core/application_runtime.py` composes a `ProviderManager`, `YukiAgent`, and `ConversationStore`.
- `create()` constructs these dependencies and opens a durable session. If session creation fails, the store is closed.
- `send_message()` persists a user turn, invokes the configured agent/provider, and persists the assistant response.
- `new_conversation()` creates and selects a new local conversation.
- `switch_conversation()` only selects a conversation already present in the local store.
- `history()` returns the active conversation history.
- `close()` closes the owned conversation database and is safe to call more than once.
- The CLI entry point now uses this lifecycle for ordinary chat and conversation switching. Existing slash commands continue through the CLI command handler.

## Data and deployment boundaries

The runtime is an in-process Python service. It is not a network server and must not be exposed directly. The conversation store remains local; the existing explicit sync preview/queue controls govern what is shared with the paired Yuki Sync Hub.

The same SQLite database must not be concurrently written by multiple machines over a shared network filesystem. Each installation owns its local database and uses the authenticated sync protocol to exchange supported events.

## Next engineering steps

1. Add a versioned local IPC/API boundary for desktop-to-runtime communication, with loopback-only binding, authentication, origin/CSRF protection, request limits and explicit tool approvals.
2. Keep provider credentials in the OS credential vault; never send them to the desktop renderer or sync hub.
3. Add structured streaming responses and cancellation without bypassing policy or audit hooks.
4. Add a desktop shell that calls the shared service rather than importing provider implementations into UI code.
5. Add an Oracle Linux service unit, reverse-proxy/TLS deployment, backups, health checks and device-specific acceptance tests.
6. Add integration tests that exercise CLI and desktop clients against the same local service contract.

## Current limitations

No desktop UI or IPC server is delivered by this change. No cloud API or public endpoint is delivered. Provider selection persistence, session restoration UI, streaming, cancellation, and multi-process coordination remain future work. The runtime tests are authored but must pass in CI and on Windows/Linux before release.
