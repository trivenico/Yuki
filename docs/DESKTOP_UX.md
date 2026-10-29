# Yuki desktop workspace UX

## Product direction

Yuki should feel familiar to people who use modern AI workspaces such as Codex and Claude Desktop without copying their branding or proprietary interface. Keep Yuki's own visual identity and make its capabilities discoverable from one workspace.

## Current first-pass layout

The desktop shell is a lightweight Tkinter application and launches the same guarded `main.py` runtime as the CLI. The first workspace pass adds:

- A compact Yuki header and local-first/approval-aware positioning.
- A persistent left navigation rail for Chat, Tasks & Plans, Memory, Skills, Plugins, Voice, Security, and System Readiness.
- A central interactive transcript and message/command composer.
- Workflow shortcuts for planning tasks, task commands, reminders, project indexing, runtime status, and help.
- Up/down command history and a shortcut to clear the visible transcript.
- Navigation entries for interactive sessions, automation, and scripting/developer workflows, alongside memory, skills, plugins, voice, security, and readiness.
- Quick actions for help, readiness, installing a declarative skill, installing an MCP plugin, and settings.
- A settings window with provider/model selection and feature configuration.

Navigation must not pretend a page exists when only a runtime command is available. Current sidebar entries invoke supported CLI commands or prefill a supported command template. This is the first step toward bringing interactive sessions, commands, automation, scripting, and developer workflows directly into the desktop; it does not yet constitute a full IDE, shell terminal, or durable automation manager. The runtime remains the single authority for model calls, tools, approvals, and policy; the desktop must not create a second tool execution path.

## UX principles

1. **One workspace:** chat, task planning, memory, extensions, settings, and security are discoverable without opening unrelated apps.
2. **Visible provider:** show the selected provider and whether it is ready; never silently switch providers.
3. **Clear task lifecycle:** planned, running, waiting for approval, completed, and failed should be distinct states once the durable task API is connected to the UI.
4. **Review before action:** show exact proposed actions and require the existing runtime's approval before side effects.
5. **Understandable permissions:** communicate whether data stays local or may be sent to a cloud provider.
6. **Useful empty/error states:** explain setup steps when Ollama, a model, credentials, optional dependencies, or an extension are missing.
7. **Accessible and responsive:** keyboard navigation, visible focus, readable contrast, resizable layout, and a useful minimum window size.
8. **Progressive disclosure:** keep the default chat simple; place advanced provider, automation, security, and developer controls in clear secondary views.
9. **Portable by design:** UI must work on supported Windows and Linux packaging/source launchers; avoid machine-specific absolute paths.
10. **No fake capabilities:** a navigation item is not proof that a feature is implemented. Mark unavailable or incomplete features honestly.

## Planned next stages

- Add a provider status indicator sourced from the same provider readiness service.
- Replace command-only task navigation with a real task inbox backed by the durable workflow/task APIs.
- Add project/workspace selection only after workspace containment and persistence are wired end to end.
- Keep the CLI and desktop as the only supported user interfaces; share application services rather than introducing a browser frontend.
- Add a review panel for proposed file changes, approvals, and execution logs.
- Improve chat transcript rendering, markdown/code presentation, message grouping, and copy controls.
- Keep interactive command history bounded, deduplicate adjacent repeats, and cover navigation with unit tests.
- Track guarded `/task` runs in a durable SQLite ledger and expose `/tasks`, `/task show ID`, plus desktop task-history/new-task shortcuts. Active execution cancellation is explicitly not claimed; only queued task records can currently be cancelled. Successful model responses remain `awaiting_verification`. The `/task verify ID --file PATH` command records file-presence/hash evidence only. Add `--contains "expected text"` to check a literal UTF-8 substring in files up to 1 MiB; this records a match or mismatch but deliberately does not mark the task `completed`. Artifact checks alone do not prove the full task goal.

For project acceptance, `/task test ID --project "relative/project/path"` requests a fresh high-risk approval before executing that project's Python `unittest` suite. It is limited to projects inside the configured Yuki workspace, uses no shell, has a 120-second timeout, and records the actual exit code and test count. A non-zero exit, timeout, or zero discovered tests leaves the task `awaiting_verification`; a passing non-empty suite can mark it complete. This is evidence for that test suite only, not a guarantee of overall correctness. Project tests are executable code and should be reviewed before approval.
- Add automated headless UI construction tests and hosted Windows/Linux package smoke tests.
- Evaluate a richer UI toolkit (for example, PySide6 or the existing optional webview approach) after comparing accessibility, packaging size, maintenance, and Windows/Linux reliability. Do not introduce a second frontend runtime until the current shared-runtime contract remains covered.

## Validation boundary

Hosted CI can validate syntax, unit tests, packaging, and runtime smoke tests. It cannot fully prove interactive Windows/Linux usability, screen-reader behavior, GPU/display quirks, microphone access, or real local Ollama inference on the user's own device. Those require explicit acceptance testing on supported Windows or Linux hardware.
