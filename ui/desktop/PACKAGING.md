# Yuki Desktop packaging

Yuki Desktop is an optional PySide6 client. Core and CLI installations do not
need Qt, PyInstaller, or keyring. Installed desktop data is stored under the
current user's platform application-data directory (Windows LocalAppData,
macOS Application Support, or Linux XDG data home). An explicit `YUKI_HOME`
overrides that location and places desktop state under `$YUKI_HOME/.yuki`.

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
not fall back to plaintext session-token storage or plaintext saved-memory
storage. Saved memory is encrypted with AES-256-GCM and its key is held in the
OS keyring; the encrypted data file is stored under the desktop profile root.

## Build an unsigned developer bundle

Build on the target operating system; PyInstaller does not provide reliable
cross-compilation between Windows, macOS, and Linux.

```bash
python -m pip install -r requirements.txt -r requirements-desktop.txt -r requirements-desktop-build.txt
python -m pip check
python -m PyInstaller --noconfirm --clean --windowed --onedir --name Yuki --paths . --collect-all PySide6 --collect-all shiboken6 --collect-all keyring --collect-submodules keyring.backends ui/desktop/launch.py
```

The output is under `dist/Yuki/` on Windows/Linux and `dist/Yuki.app/` on macOS. Keep the complete directory or app bundle; the executable depends on its bundled support files.

## CI artifacts

The `Yuki quality gates` workflow builds on native Windows, macOS, and Linux
runners, executes the packaged binary in Qt offscreen smoke-test mode, and
uploads short-lived unsigned developer bundles together with a CycloneDX SBOM
for the build environment and a portable SHA-256 manifest for
every regular bundle file. The manifest records relative paths, sizes, file
modes, hashes, source commit/ref, and platform metadata; it does not contain
absolute build paths. These artifacts are for developer validation, not public release.
The workflow does not sign, notarize, publish, or auto-update the application.

## Release blockers

Before distributing a production installer, complete and review:

- Real clean-machine install, launch, login, logout, upgrade, uninstall, and
  rollback tests on each supported OS.
- Qt/PySide6 and transitive dependency license and notice review.
- macOS code signing and notarization; Windows signing; Linux package strategy.
- OS credential-store behavior on clean user profiles and supported Linux
  desktop environments.
- Migration of data from earlier repository-local desktop installs, backup/restore, crash recovery, and safe uninstall.
- Artifact provenance, checksum publication, vulnerability and secret scans.
- GUI tests for provider failure, stale approvals, keyboard accessibility,
  shutdown, and in-flight work.

No signing keys or release credentials belong in repository files or build logs.
