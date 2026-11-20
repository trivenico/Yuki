# Phase 112 — Desktop task-session lifecycle integration

## Scope

This phase closes the integration gap between the Phase 110/111 task-approval
session controls and the single-owner desktop lifecycle. It does not start a
background worker or enable autonomous task execution.

## Changes

- CompanionApplicationService.revoke_session_tasks() now requires both the
  authenticated actor ID and exact session ID, and forwards both as keyword
  arguments to TaskRuntime.revoke_session_approvals().
- DesktopCompanionController.logout() supplies the actor and session from its
  already-bound AuthenticatedDesktopIdentity.
- Desktop task runtime construction now injects task_session_verifier() using
  the current OS-keyring-backed desktop session. Approval and revocation
  therefore verify the exact currently active session instead of accepting a
  caller-supplied session identifier without independent verification.
- Added regression tests for exact actor/session forwarding and the
  no-task-runtime no-op path.

## Shutdown order

1. The controller asks the task runtime to fence the exact live session and
   cancel its queued/running approved tasks.
2. The desktop session manager revokes the authentication credential even if
   task cleanup reports an error.
3. Conversation and memory context are reset/disabled.
4. The desktop service lifecycle closes owned resources in reverse dependency
   order and retains failed cleanup hooks for retry.

If task-session revocation fails, logout reports incomplete cleanup and
does not revoke the authentication credential. The desktop action gate remains
closed, but the live session is retained solely so cleanup can retry the durable
task fence. Only after that fence commits does logout revoke authentication and
continue cleanup. If authentication revocation then fails, the task fence is
already durable, so retrying credential revocation is safe.

## Worker boundary

The desktop currently constructs the task runtime and specialist coordinator,
but does not attach or start LocalWorkerRuntime / WorkerSupervisor as an
always-running service. This phase does not silently introduce background
execution. The local worker runtime remains an explicit, separately composed
finite-cycle service.

## Verification status

- Regression tests added; no local test runner was available in this environment.
- CI must be inspected on the resulting PR head before claiming test success.
- No merge or deployment performed.
