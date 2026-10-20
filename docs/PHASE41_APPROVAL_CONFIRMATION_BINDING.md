# Phase 41 — Approval confirmation binding audit

## Finding

The coordinator currently asks the human to confirm an action and only afterward creates a dispatcher approval token. Although it copies the stored arguments, this ordering makes the approval relationship implicit: the durable pending request and the dispatcher token are separate records, and the token is minted after the decision rather than being the token for the reviewed action.

## Required fix

The coordinator should:
1. Validate the stored action against the current registered manifest.
2. Create the dispatcher token for the exact immutable action snapshot before invoking the confirmation callback.
3. Present the action, risk, required capabilities, payload digest, and expiry to the user. Never expose the bearer token to the UI.
4. If the callback denies, throws, or is interrupted, revoke the prepared token.
5. If approved, consume that same token and execute its stored snapshot; never mint a replacement token after approval.
6. Keep the existing actor/session binding on the pending request and audit the outcome without logging arguments or bearer tokens.

## Current phase status

The repository audit identified this ordering gap. The attempted source update was blocked by the connected repository operation's safety checks, so this file records the finding and exact acceptance criteria rather than claiming the source fix is committed. Phase 41 is not complete until the coordinator implementation and regression tests land and latest-head CI passes.

## Additional blockers

- The pending request and dispatcher token remain separate SQLite/in-memory or SQLite/SQLite state transitions.
- Production configuration must inject the durable store explicitly.
- Legacy `YukiCoreAgent.run_skill()` still executes in-process objects.
- Subprocess execution is not a complete OS sandbox.
