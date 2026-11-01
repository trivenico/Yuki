# User interface

## Current interfaces

- The guarded terminal entry point in `main.py` is the baseline interface.
- `ui/dashboard.py` is an optional Textual supervision interface. It is not a native desktop application.
- The shared companion application boundary lives in `companion/application.py`; desktop and CLI clients must use this service rather than duplicate provider routing, memory policy, or task orchestration.

## Native desktop direction

The first native desktop vertical slice is planned as an optional PySide6 client that composes the existing Python application services in-process. The architecture, security invariants, first-slice scope, packaging gates, and test plan are documented in [Desktop application architecture](../docs/DESKTOP_ARCHITECTURE.md).

No graphical desktop application, native installer, or cross-platform packaged release is implemented yet. Do not treat the Textual dashboard or a design mockup as a desktop release.

Desktop requirements:

- Keep PySide6 optional; CLI-only installation must not import it.
- Use one authenticated composition root and the shared `CompanionApplicationService`.
- Never call provider SDKs or task stores from widgets.
- Keep plan proposal, dispatch, approval, and worker execution as separate explicit actions.
- Preserve memory consent, authorization, audit, and redaction boundaries.
- Run provider and filesystem work off the UI thread.
- Ship only after clean-install packaging and security tests on each claimed platform.
