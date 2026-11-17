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
python yuki_cli.py auth bootstrap-admin
python -m ui.desktop.launch
```

Complete sign-in in the desktop window. The CLI bootstrap command creates the owner account only; it does not create a CLI session token. Desktop sessions are stored in the operating-system credential store.

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

## CI coverage and release blockers

The `Native desktop packages` workflow builds unsigned PyInstaller
bundles on native Windows, macOS, and Linux runners. Each packaged executable
is launched with `--smoke-test`, which verifies Qt widget initialization and
loads the bundled first-run configuration template into an isolated temporary
profile. The workflow also emits a CycloneDX SBOM for the Python build
environment, SHA-256 checksums for bundle files, and short-lived CI artifacts.
Build provenance is attested for platform artifacts on pushes to `main`.

These are CI/developer bundles, not signed production installers. The current
SBOM describes the Python environment and is not a complete inventory of all
OS-level libraries and binaries embedded by PyInstaller. A hosted-runner smoke
test does not replace installation and acceptance on independent clean
physical machines.

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
