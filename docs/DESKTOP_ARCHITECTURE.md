# Desktop application architecture

**Status:** architecture accepted; D1 import-safe package and D2 source implementation are present. The desktop now has an OS-keyring session manager, authenticated controller, login/chat window, and a window-scoped background provider worker using the shared companion service. The controller now exposes identity-bound specialist listing, plan proposal/inspection/dispatch/cancellation, task summaries/progress, and version-bound task approval/cancellation methods. The launcher composes the existing authorized task runtime and specialist coordinator. Controller/session tests cover proposal-versus-dispatch and actor-scoped task access. The desktop now includes Chat, Specialists, Plans, and Tasks tabs. The specialist view lists only registered specialists; the plan view uses a background worker for proposals and requires explicit confirmation to dispatch; the task view displays redacted progress events, supports version-checked cancellation, and provides a task approval review dialog. The dialog retrieves the authorized stored definition, displays each step's action, exact argument, approval requirement and declared permissions, verifies the task version/state again, and requires an explicit confirmation before binding approval to the active session. Approval does not start a worker. The PySide6 UI has not been exercised in a real desktop runtime or clean OS images; packaging remains unfinished.
**Parent work:** companion service and structured provider chat on `feat/structured-provider-chat`.
**Decision owner:** Yuki maintainers.

## Decision

Build the first native desktop client as an optional **PySide6** application that composes the existing Python application services in-process. Keep the desktop shell thin. Do not introduce a second provider client, memory implementation, task database, authorization system, or worker runtime.

This is the lowest-risk first slice for the current Python-first repository: it avoids exposing a local HTTP listener or inventing a desktop-to-backend authentication protocol before Yuki has a stable desktop identity/session lifecycle. It also reuses the Python service boundary already shared by the companion work.

This is an implementation direction, not a claim that a desktop app or distributable exists. Re-evaluate Tauri only if a web-technology UI, frontend hiring needs, or a separate backend process becomes a concrete requirement.

## Non-goals

- No always-on background agent, automatic task dispatch, or implicit worker execution.
- No direct provider SDK calls from UI widgets.
- No direct SQLite access from the UI.
- No permission grants based on selected companion role or specialist name.
- No arbitrary shell, Python, browser, filesystem, or network execution from model output.
- No cloud sync or remote control plane in the first desktop milestone.
- No requirement that PySide6 be installed for CLI-only users.

## Layering and ownership

```text
PySide6 presentation
  ├── Chat view
  ├── Specialist / plan review
  ├── Task activity and event timeline
  └── Settings, memory consent, approvals
          |
          v
Desktop controller / view models
  ├── UI-thread dispatch and cancellation
  ├── typed presentation state
  └── error mapping (no raw secrets or provider traces)
          |
          v
Authenticated desktop composition root
  ├── resolve local identity and session
  ├── construct configured provider
  ├── construct shared CompanionApplicationService
  └── inject existing permission/approval callbacks
          |
          v
Existing application and domain services
  ├── CompanionApplicationService
  ├── provider gateway / structured chat
  ├── consent-scoped memory
  ├── SpecialistAgentCoordinator
  ├── TaskRuntime / workflow store
  └── audit and policy services
```

The composition root is the only place that may assemble dependencies. Widgets receive narrow controller interfaces, not raw stores, provider SDK clients, or unrestricted runtime objects. The same application-service instances must be used for chat, plans, task summaries, approvals, and progress updates within a desktop session.

The UI thread must never perform provider calls, filesystem indexing, or worker cycles synchronously. Use a window-scoped bounded background worker pool and marshal results back to the UI thread. The initial chat shell prevents closing or signing out while a provider request is active; cancellation and graceful shutdown are follow-up hardening work. Closing the window must never silently cancel durable tasks or start workers.

## First vertical slice

1. Startup diagnostics show provider readiness, selected local/cloud route, and current identity without exposing credentials.
2. Chat uses `CompanionApplicationService.respond()`, preserves structured user/assistant roles, and renders provider failures as safe actionable errors.
3. Memory is visibly off until the user enables it; session-context consent is distinct from saving a memory.
4. Specialist catalog is read-only. Role selection changes response guidance only and never grants capabilities.
5. A model-generated plan is shown as a proposal. Dispatch is a separate explicit action and queues only through the coordinator.
6. Tasks show bounded redacted summaries and incremental progress. Approval displays task name, requested capability, target/workspace, risk, version, and the exact action before confirmation.
7. Worker execution is a separate operator action, disabled by default in the desktop slice. No background execution is started by app launch.

## Security and privacy invariants

- Resolve actor identity from the authenticated local session. Never accept actor IDs from widgets, model output, or editable form fields.
- Reuse the existing authorization callbacks and permission gate. Fail closed when identity, session, or policy is unavailable.
- Bind approvals to the exact task ID, current version, authenticated session, and displayed action. A stale view must refresh rather than approve.
- A companion role is presentation context, not an authorization principal.
- Treat provider output, conversation history, imported skills, memory entries, and task descriptions as untrusted data.
- Keep secrets in environment variables or the OS credential store; never put them in Qt settings, logs, crash reports, or UI state snapshots.
- Store desktop preferences separately from private task/conversation data. Respect the existing user-data root and file-permission rules.
- Desktop saved memory uses AES-256-GCM with its encryption key held in the OS keyring; the encrypted file lives under the private desktop data root, never the process working directory. If secure keyring or cryptography support is unavailable, startup fails closed rather than writing plaintext memory.
- Before opening authentication, task, or memory stores, desktop startup creates and verifies its profile data root. POSIX systems require owner-only `0700` permissions and current-user ownership; failures abort startup. Windows uses the user application-data directory and its OS ACL rather than relying on POSIX mode bits.
- Make memory save consent and memory-context consent separate, visible, revocable controls.
- Redact provider exceptions and task event payloads at the application boundary. Do not display raw stack traces by default.
- Do not expose a local network server in the first slice. If a future process boundary is required, design authenticated IPC, origin restrictions, replay protection, and lifecycle ownership as a separate security review.

## Packaging and platform strategy

- Keep PySide6 in a dedicated optional dependency manifest; core and CLI installs remain lightweight.
- Target Windows, macOS, and Linux, but claim support only after clean-machine smoke tests on each OS.
- Evaluate PyInstaller or Nuitka only after a minimal launch/chat vertical slice works from source. Build artifacts in CI on native runners.
- Sign/notarize installers where the platform and release credentials allow. Document unsigned developer builds honestly.
- Verify upgrade, rollback, data-directory migration, crash recovery, and uninstall behavior before calling the desktop release production-ready.
- Review PySide6/Qt licensing, notices, plugin bundling, and redistribution obligations before shipping binaries.

## Test plan and release gates

- Controller tests use fake application services and assert that no widget calls a provider or store directly.
- Contract tests prove the desktop composition root injects the same authorization and service dependencies as the CLI.
- UI tests cover provider unavailable, signed out, consent off/on, stale approval version, denied approval, cancellation, and window close during an in-flight request.
- Security tests verify that role selection cannot grant capabilities and that model-produced plans cannot dispatch or execute without explicit user actions.
- Packaging smoke tests run on clean Windows/macOS/Linux environments with no developer Python environment.
- Accessibility checks cover keyboard navigation, focus order, readable status, high contrast, and screen-reader labels.
- Release gates include dependency/license audit, secret scan, data export/deletion checks, and a documented rollback procedure.

## Milestones

| Milestone | Deliverable | Exit condition |
|---|---|---|
| D1 | Optional desktop package and composition root | Imports cleanly without PySide6 installed; fails with a clear install hint when launched without it |
| D2 | Chat and consent UX | Uses shared companion service; provider calls stay off the UI thread |
| D3 | Specialist and plan review | Registered-specialist catalog, background plan proposal, review, explicit dispatch and cancellation views implemented; UI runtime verification pending |
| D4 | Task activity and approval UX | Redacted progress, version-checked cancellation, exact-definition review dialog, and session-bound approval implemented in source; UI runtime verification pending |
| D5 | Native packaging | Optional PyInstaller build manifest, packaging instructions, and native Windows/macOS/Linux CI artifacts added; clean-machine installation and launch smoke tests remain pending |
| D6 | Release qualification | Security, privacy, accessibility, upgrade, rollback and license gates pass |

Keep PySide6 and keyring in the optional desktop manifest; never add them to core requirements or bundle a worker daemon as part of the desktop shell.
