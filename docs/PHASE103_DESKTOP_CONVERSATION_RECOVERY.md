# Phase 103 — Desktop conversation recovery and navigation

## Implemented on feat/agent-workforce-registry

- Added `start_new_conversation` to the authenticated conversation facade, shared chat controller and desktop controller bridge.
- Kept list, select, history and create operations behind the live authenticated facade. The underlying conversation adapter scopes list and history queries to its immutable owner ID; selection resolves the conversation using the same owner ID.
- Added a desktop conversation selector, **New chat** and **Refresh history** controls.
- Selecting a conversation loads up to 200 persisted messages. Creating a conversation clears the transcript and selects the new owner-scoped conversation.
- On sign-in, Yuki restores the most recently updated conversation and populates the selector.
- Sign-out clears the selector and disables conversation navigation controls.
- Added bridge-level tests for navigation operations and for a simulated cross-owner selection denial causing fail-closed logout of both chat and specialist sessions.

## Security properties

- The UI never supplies an owner ID.
- The authenticated facade checks the live session before each operation.
- The storage adapter uses the authenticated owner's ID for list, select and history operations.
- A permission failure is handled by the bridge's existing fail-closed cleanup path.
- Conversation history remains durable data in the conversation store, not disposable Memory Fabric cache.
- Logout clears rendered history and disables navigation controls.

## Verification status

- Updated source and test files were fetched back from GitHub after writes.
- Automated tests have not been executed in this environment.
- Latest PR workflow-run query returned no runs for the current head; this is not a passing CI result.
- Full Qt interaction, clean-install restart recovery and two-account isolation still require CI or real desktop validation.

## Next acceptance gates

1. Run `python -m unittest tests.test_shared_desktop_composition -v` and the full quality workflow.
2. Add integration coverage using two authenticated identities against one durable conversation store.
3. Verify an empty account opens a clean new conversation without a stale selected ID.
4. Verify restart restores the latest conversation in stable chronological message order.
5. Verify sign-out clears all visible history and a second account cannot enumerate, select or read the first account's conversations.
6. Exercise New chat, history selection, refresh and provider failure through Qt smoke/manual testing.
