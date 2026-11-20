# Phase 109 — Owner Authority Runtime Audit

Status: audit complete; enforcement integration remains in progress.

## Scope inspected

The current branch was inspected at `f79951086ccd8f19b0bef572d335eb4c1ebaf8b0`:

- `core/auth.py`
- `automation/task_runtime.py`
- `automation/agent_coordinator.py`
- `ui/desktop/authenticated_conversation.py`
- `ui/desktop/shared_desktop_controller.py`
- `core/profile_identity.py`

## Existing protections confirmed in source

1. The desktop conversation facade rechecks the exact current session identity on each operation and again before releasing a completed response.
2. The shared desktop bridge closes both authenticated surfaces after a `PermissionError`.
3. `TaskRuntime` denies operations when its authorization callback is absent or raises, and each task operation checks a named capability.
4. Specialist-agent planning and dispatch require an injected authorization callback; agent registry and plan validation are bounded.
5. Task approval is persisted with an actor ID and session ID, and session-revocation support cancels queued work approved by that session.
6. The local profile owner binding is checked before the desktop runtime is initialized.

## Integration gap identified

`TaskRuntime.approve()` accepts a `session_id` and persists it with the approval, but its authorization callback currently receives only `(actor_id, action, task_id)`. The callback therefore cannot itself prove that the supplied session ID is the currently live session. The durable store can bind an approval to the supplied session identifier, but that is not equivalent to authenticating the identifier at the runtime boundary.

This is an integration gap, not evidence that an unauthenticated remote endpoint is currently exposed. It must be closed before task approval is considered fully session-authenticated.

## Phase 110 implementation requirements

- Add a separately injected live-session verifier to the task runtime; do not trust a caller-supplied actor or session ID by itself.
- For approval, verify the exact current session token resolves to the expected owner and matches the approval session identifier before changing durable task state.
- Fail closed if the verifier is absent, unavailable, expired, revoked, or raises.
- Keep the existing authorization callback for capability checks; authentication and authorization remain distinct checks.
- Apply the same owner/session verification at the desktop specialist-controller boundary and every externally reachable task/approval surface.
- Add regression tests for a valid owner session, expired session, revoked session, different session for the same owner, forged actor ID, verifier exception, and concurrent logout/approval.
- Do not expose a new approval HTTP endpoint as part of this work. Keep destructive, financial, external communication, credential, security-sensitive, and deployment actions behind explicit confirmation.
- Do not treat voice match, screen observation, browser content, documents, or tool output as owner authentication.

## CI observation

A GitHub Actions wake-word test job previously failed because a test attempted to join a worker thread after it had already been cleared. The currently fetched `tests/test_wakeword_session.py` uses a shared failure helper that checks for a non-null worker before joining. This source inspection is not a rerun of the failed workflow and does not establish that the job now passes.

## Verification limits

This phase was a source-level audit only. No local test suite was run in this environment. No production deployment or PR merge was performed.
