# Yuki Desktop packaging

Yuki Desktop is an optional PySide6 client. Core and CLI installations do not
need Qt, PyInstaller, or keyring.

## Local source run

```bash
python -m pip install -r requirements.txt -r requirements-desktop.txt
python -m ui.desktop.launch
```

Create the first local account through the CLI before signing in:

```bash
yuki auth bootstrap-admin
```

A supported operating-system credential store is required. The desktop does
not fall back to plaintext session-token storage.

## Build an unsigned developer bundle

Build on the target operating system; PyInstaller does not provide reliable
cross-compilation between Windows, macOS, and Linux.

```bash
python -m pip install -r requirements.txt -r requirements-desktop.txt -r requirements-desktop-build.txt
python -m pip check
python -m PyInstaller --noconfirm --clean --windowed --onedir --name Yuki --paths . --collect-all PySide6 --collect-all shiboken6 --collect-all keyring --collect-submodules keyring.backends ui/desktop/launch.py
```

The output is under `dist/Yuki/` (`Yuki.exe` on Windows). Keep the complete
directory; the executable depends on its bundled support files.

## CI artifacts

The `Yuki desktop build` workflow builds on native Windows, macOS, and Linux
runners and uploads short-lived unsigned developer bundles. These artifacts
are for smoke testing, not public release. The workflow does not sign, notarize,
publish, or auto-update the application.

## Release blockers

Before distributing a production installer, complete and review:

- Real clean-machine install, launch, login, logout, upgrade, uninstall, and
  rollback tests on each supported OS.
- Qt/PySide6 and transitive dependency license and notice review.
- macOS code signing and notarization; Windows signing; Linux package strategy.
- OS credential-store behavior on clean user profiles and supported Linux
  desktop environments.
- User-data migration, backup/restore, crash recovery, and safe uninstall.
- Artifact provenance, checksum publication, vulnerability and secret scans.
- GUI tests for provider failure, stale approvals, keyboard accessibility,
  shutdown, and in-flight work.

No signing keys or release credentials belong in repository files or build logs.
