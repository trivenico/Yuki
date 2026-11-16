# Desktop and canonical conversation runtime integration

## Current branch topology

The desktop foundation and shared-conversation work are intentionally still separate:

- Desktop foundation: PR #426, branch `feat/desktop-architecture-foundation`, head `062f3cdbab12c8c1384bbdacfc5a09fc6fa93610`.
- Canonical conversation runtime: PR #999, branch `feat/desktop-shared-conversation-adapter`, head `0d1f467bfef1377f687e4158fdc1f3ce279333dd`, based on `feat/owner-agent-execution-component` (PR #887).
- PR #426 is based on `feat/structured-provider-chat`.

A GitHub compare of the two current heads reports a diverged history (351 commits ahead on the #426 side and 360 commits ahead on the #999 side, with merge base `dd9c88b524bf6e891ce0e92f2324ca4184ab3ae6`). This is not a safe candidate for a blind retarget, merge, or bulk cherry-pick.

## Concrete integration blockers

The desktop foundation branch contains the legacy desktop composition and UI:

- `ui/desktop/launch.py`, `auth.py`, `bootstrap.py`, `controller.py`, `lifecycle.py`, `memory.py`, `storage.py`, `backup.py`, and `migration.py`.
- `companion/application.py`, `conversation.py`, `conversation_store.py`, `memory_consent.py`, and `secure_memory.py`.
- Specialist planning, task approval, backup, restore, and migration workflows are wired through the existing controller and must remain available.

The canonical runtime branch contains the new shared-conversation seam:

- `core/conversation_application_runtime.py` and `core/conversation_service.py`.
- `ui/desktop/conversation_adapter.py`, `runtime.py`, `authenticated_conversation.py`, `shared_conversation_session.py`, `shared_chat_controller.py`, `shared_desktop_controller.py`, and `shared_desktop_composition.py).

At the inspected branch heads, the canonical runtime files are not present in the desktop foundation branch, while the legacy `companion/application.py` and `ui/desktop/launch.py` are not present in the canonical runtime branch. Consequently, neither branch alone can run the complete intended desktop composition.

## Integration sequence

1. **Stabilize the canonical runtime contract.** Keep the Qt-independent adapter, authenticated session facade, shared chat bridge, and composition factory tested independently of PySide6. The owner ID must continue to come only from the authenticated identity; no UI-supplied owner ID or raw token.
2. **Reconcile the runtime dependency chain.** Integrate the exact core runtime/service commits and their required dependencies into a dedicated integration branch based on the current desktop foundation. Do not merge the two diverged branch histories wholesale.
3. **Preserve legacy desktop capabilities.** Keep the existing specialist controller as the delegated specialist surface. The shared desktop bridge must route chat/memory to the canonical runtime and task planning, approvals, backup, restore, and migration to the existing controller.
4. **Cut over the launcher.** Build the shared desktop composition after successful local authentication. Ensure the outer desktop lifecycle owns provider shutdown, the per-session bundle owns its runtime, and logout/expiry closes the session bundle exactly once.
5. **Test the actual Qt boundary.** Add launcher-level tests for login, chat, memory-consent toggle, session expiry during chat and specialist work, logout, close-event cleanup, and worker-thread signal delivery. Run the supported Linux, Windows, and macOS packaging/test matrix.
6. **Promote only after integration.** Keep the component PRs draft until the integration branch passes the full test matrix and a security review confirms no duplicate lifecycle owner, stale authenticated UI, or tool execution bypass.

## Non-negotiable safety properties

- Session expiry closes the authorization gate before cleanup and never leaves the previous transcript visible.
- Failed cleanup is surfaced; the application does not claim the session was safely closed.
- Provider shutdown has exactly one owner.
- The desktop worker remains the only place that performs blocking model work; Qt widgets are updated only on the UI thread.
- Tool execution remains denied in the desktop conversation runtime until a separately reviewed, authenticated, permission-scoped tool bridge is intentionally introduced.
- Specialist task dispatch and approval semantics are preserved; approving a task does not silently start a worker.

## Status

The two component branches have passing checks on their latest inspected heads, with some checks still queued or running. That is useful component evidence, not proof that the integrated desktop application works. No merge is authorized by this document.
