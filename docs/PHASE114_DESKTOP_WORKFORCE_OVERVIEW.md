# Phase 114 — Desktop workforce overview

## Delivered

- Added a Workforce tab to the authenticated desktop workspace.
- Displays the registered specialist profiles and their enabled state/capabilities.
- Displays a bounded snapshot of recent task counts by queued, approval, running,
  succeeded, failed and cancelled states.
- Refreshes on workspace initialization and through an explicit Refresh workforce
  action.
- Uses the existing authenticated DesktopCompanionController facade; it does not
  access stores or provider clients directly from widgets.

## Deliberate execution boundary

The dashboard explicitly reports that the ManagedWorkerService is not attached
to the desktop session and that no background workers are running. Start/pause/
stop controls are not exposed yet.

The reason is an integration boundary found during implementation: desktop
composition currently owns `tasks.sqlite3`, a session-bound TaskRuntime and
SpecialistAgentCoordinator. `build_local_worker_runtime()` separately creates
`yuki_tasks.sqlite3`, a process-scoped local operator and approval capability.
Connecting that worker directly would create a second queue and a distinct
authorization/session domain. A superficial Start button would therefore not
execute the tasks shown in the desktop dashboard and could mislead the owner.

## Required next integration

Before enabling execution, unify the worker and desktop around one task database
and one explicitly verified owner-session approval model. Worker-only completion
and lease-recovery permissions must remain unavailable to the owner-facing UI.
Worker shutdown must fence the active approval session, close cycle admission,
and drain in-flight provider work before dependent resources close.

## Verification

- UI implementation pushed to the existing draft PR.
- No local Qt/test runner was available; no tests are claimed as executed.
- CI must be checked on the latest PR head.
- No worker was started; no merge or deployment performed.
