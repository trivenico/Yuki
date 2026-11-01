# User interface

## Current interfaces

- The guarded terminal entry point in `main.py` is the baseline interface.
- `ui/dashboard.py` is an optional Textual supervision interface. It is not a native desktop application.
- The shared companion application boundary lives in `companion/application.py`; desktop and CLI clients must use this service rather than duplicate provider routing, memory policy, or task orchestration.

## Native desktop direction

The first native desktop vertical slice is implemented in source as an optional PySide6 client. It uses the existing local Yuki account database, stores its opaque session token in the OS credential store, and calls the shared `CompanionApplicationService` through a Qt-independent controller. Provider requests run in a window-scoped background worker. The architecture, security invariants, packaging gates, and test plan are documented in [Desktop application architecture](../docs/DESKTOP_ARCHITECTURE.md).

The desktop source includes Chat, Specialists, Plans, and Tasks tabs. Plan proposals run in a background worker; dispatch requires an explicit confirmation. Task progress is shown through the redacted companion task-update adapter, and cancellation uses an expected task version. Task approval is intentionally not exposed in the UI until the exact workflow action can be rendered safely for confirmation. The PySide6 UI has not yet been exercised in a real desktop runtime or clean OS image. There is no native installer or cross-platform packaged release. Do not treat the Textual dashboard or a design mockup as a desktop release.

### Run the desktop from source

1. Create the first local account once: `yuki auth bootstrap-admin`.
2. Install the optional desktop dependencies: `python -m pip install -r requirements-desktop.txt`.
3. Configure Yuki's provider using the same `config.yaml` and environment variables as the CLI.
4. Start the UI: `python -m ui.desktop.launch`.

A supported OS credential store must be available (Windows Credential Locker, macOS Keychain, or Linux Secret Service). The desktop does not provide a plaintext token fallback. If your configured provider needs an API key, set it in the environment before launch.

Desktop requirements:

- Keep PySide6 optional; CLI-only installation must not import it.
- Use one authenticated composition root and the shared `CompanionApplicationService`.
- Store desktop session tokens only in a supported OS credential store through `DesktopSessionManager`; never persist passwords or tokens in Qt settings or plaintext files.
- Reuse `BuiltInAuthService` for account/session validation. A local identity object alone is not proof of authentication.
- Never call provider SDKs or task stores from widgets.
- Keep plan proposal, dispatch, approval, and worker execution as separate explicit actions.
- Preserve memory consent, authorization, audit, and redaction boundaries.
- Run provider and filesystem work off the UI thread.
- Ship only after clean-install packaging and security tests on each claimed platform.
