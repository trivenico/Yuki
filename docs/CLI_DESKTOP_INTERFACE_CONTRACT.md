# Yuki CLI and desktop interface contract

**Status:** architecture and delivery contract; not a claim that every listed workflow is implemented.

Yuki is a private, owner-operated assistant. The CLI and native desktop app are first-class interfaces alongside the authenticated private web interface. They must remain clients of the same application capabilities, not independent assistant implementations.

## Product responsibilities

### CLI — fast operator and automation surface

The stable `yuki` console entry point should serve:
- provider setup, status, diagnostics and safe configuration;
- persistent conversations and conversation selection;
- task/goal inspection, creation, cancellation and recovery;
- approval review and explicit confirmation for consequential actions;
- memory inspection, export, retention and deletion;
- backup/restore, migration, health and security diagnostics;
- scripting-friendly JSON output, stable exit codes and non-interactive operation.

CLI commands must distinguish read-only operations from mutations. Consequential operations require explicit owner confirmation, and non-interactive execution must fail closed when required confirmation is unavailable. Never put credentials in command arguments, shell history, logs or process listings. Prefer OS credential stores or protected interactive input. Shell execution, arbitrary plugin loading and generated-code execution are not implicitly granted by running the CLI.

**Current implementation observed:** `yuki_cli.py` provides `doctor`, `setup`, provider status/test, `ask`, persistent `chat`, and local owner bootstrap. `core/local_cli_runtime.py` composes the shared conversation application runtime but deliberately supplies an empty deny-all tool registry. Thus persistent chat currently has no tool execution. The broader task/approval/memory administration command surface described above is future work, not a present capability.

### Desktop — everyday personal workspace

The desktop app should provide:
- authenticated owner sign-in and explicit sign-out;
- durable conversation history and streaming/interruption feedback;
- task/goal workspace with progress, approvals and result review;
- memory controls and visible retention/deletion actions;
- provider/model settings without exposing secrets;
- optional voice capture/playback with visible recording state and consent;
- health, backup/restore and security diagnostics;
- accessible keyboard navigation, safe notifications and reliable shutdown.

Widgets must call application services/controllers; they must not implement provider routing, persistence, authorization or task state independently. Background workers must not access Qt widgets directly. Sign-out, session expiry and shutdown must revoke access before releasing resources, and failed cleanup must remain visible and retryable.

**Current implementation observed:** `ui/desktop/launch.py` owns native Qt startup and service lifecycle. `ui/desktop/runtime.py` composes durable owner-scoped chat with tools denied by default. `ui/desktop/shared_desktop_composition.py` bridges the durable chat controller with existing specialist/task services. Authenticated desktop composition, session expiry, shutdown races, encrypted memory and lifecycle retry have dedicated tests. The passive security dashboard document explicitly says that panel is not yet integrated into the main window. Treat these boundaries honestly when describing the UI.

## Shared application contract

All interfaces must converge on the same domain/application services for:
1. owner identity and session validation;
2. conversation, memory and profile access;
3. durable tasks, goals, dependencies and cancellation;
4. central authorization, approvals and append-only audit;
5. provider selection, inference limits and cancellation;
6. MCP/integration connections and scoped credentials;
7. evaluation, health, backup and recovery.

The UI layer may format and collect input, but must not become a second source of truth. Owner identity is derived from the authenticated session or protected local owner identity—not model output, a widget field or a caller-supplied owner ID. Each interface must pass the same policy and audit context to actions. A capability unavailable in one interface must be reported as unavailable; it must not be reimplemented with weaker controls.

## Platform and packaging policy

- Linux is the first production-pilot target; keep Python and application services portable so Windows desktop and CLI remain supported and macOS packaging remains testable.
- Keep core/CLI installs free of Qt and desktop-only dependencies. Desktop and voice dependencies remain optional extras.
- Store data in the platform user-data directory or an explicit protected `YUKI_HOME`; never inside the installed source/package directory.
- Keep credentials in the OS credential store. Do not silently fall back to plaintext tokens or keys.
- Native packaging is per operating system. Current GitHub Actions builds unsigned PyInstaller bundles for Linux, Windows and macOS, includes a Python-environment SBOM and SHA-256 checksums, and attests main-branch artifacts. These are developer/CI bundles, not signed production installers. The SBOM is not yet a complete inventory of embedded OS libraries.
- Before production desktop release, require clean-machine install/upgrade/rollback/uninstall acceptance, signing/notarization strategy, complete notices/SBOM review, OS keyring validation, accessibility checks, backup/restore, and crash/recovery testing.
- CLI release acceptance must cover clean installation, `yuki` entry point, shell completion/help, stable JSON and exit codes, protected data paths, credential handling, non-interactive fail-closed behavior, and supported Python versions.

## Delivery sequence

1. **Consolidate contracts:** keep CLI and desktop on the same conversation/task/approval application services and central authorization. Remove duplicated domain decisions from interface code.
2. **CLI operator workflows:** add read-only task/goal/approval/memory/health commands first; add guarded mutations with explicit confirmation, stable JSON and idempotent request IDs.
3. **Desktop integration:** complete the main-window task/goal, approval, memory and diagnostics surfaces using existing controllers; integrate the passive security dashboard only when its lifecycle and sensor services are approved.
4. **Cross-interface acceptance:** create the same goal/conversation in one interface and verify state, audit, cancellation and approval visibility in the others. Test concurrent CLI/desktop actions and stale-session rejection.
5. **Release readiness:** Linux-first clean install and upgrade/rollback, then Windows/macOS packaging acceptance. Keep unsigned artifacts out of production use.

## Non-negotiable invariants

- One owner, one authorization policy, one durable task state and one audit trail.
- No hidden tool access because a user chose CLI or desktop instead of web.
- No automatic approval in scripts, background jobs or model-generated plans.
- No production credentials in CI, notebooks, desktop logs or browser workers.
- No public listener or public account registration as part of this private product.
- Interface-specific failures must not corrupt shared durable state.
