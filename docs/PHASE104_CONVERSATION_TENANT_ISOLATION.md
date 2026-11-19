# Phase 104 — Conversation tenant isolation

## Implemented

- Removed a duplicate method definition from the authenticated desktop conversation facade.
- Fixed history refresh to preserve the current selection rather than visually selecting the first row while the controller remains on another conversation.
- Ensured the history controls stay disabled if a chat worker completes after logout has already cleared the controller.
- Added `tests/test_conversation_owner_isolation.py`, an integration-style persistence test using one SQLite database and two independent owner IDs.
- The test creates conversations and messages for two accounts, verifies each account lists only its own conversations, and verifies cross-owner conversation lookup and message history raise `ConversationNotFound`.
- Added a third-account check confirming an account with no conversations receives an empty list rather than another tenant's data.

## Verification status

- The authenticated facade and new test file were fetched back from GitHub and inspected.
- Confirmed exactly one `start_new_conversation` definition remains in the facade.
- Tests have not been executed in this environment.
- The latest workflow-run query returned no runs for the current PR head. This does not establish CI success.
- This test validates the SQLite owner-filtering boundary; a full live-session facade test and Qt sign-in/account-switch test remain required.

## Next acceptance gates

1. Execute the focused tests and full repository quality workflow.
2. Test two real authenticated sessions sharing one database, including facade list/select/history behavior.
3. Exercise sign-out and account switching through the Qt desktop.
4. Verify startup restoration and ordering with a populated database after process restart.
5. Keep JARVIS feature expansion behind these data-isolation and recovery gates.
